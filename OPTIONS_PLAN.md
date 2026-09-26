# Teasel Options Payoff — Build Plan

Add a manual-entry options payoff visualizer to Teasel as a second page next
to the Kalshi ladder tool. It stays on the Free plan, has no market-data feed,
and lives on the same service and subdomain.

> Status: built on branch `feature/options-payoff` (not yet deployed; see
> `deploy/OPTIONS_ROLLOUT.md`). Implementation details, exact contracts and
> golden test values are in `OPTIONS_BUILD_INSTRUCTIONS.md`, which takes
> precedence where the two differ. Follows the same rules as `SPEC.md`: a pure,
> framework-free, fully tested engine; all math on the server; the browser only
> renders.

---

## 0. Decisions already made

| Decision | Choice | Why |
|---|---|---|
| Separate tool or Teasel page | Teasel page (`/options`) | Free, no data cost, no new service to run |
| Data source | User enters every leg manually | No OPRA/vendor licensing, no fetch endpoint |
| Plan | Free (inherits Teasel's `TOOL_MIN_PLAN`) | Front door for signups |
| Scope of payoff | At expiry, single expiration | Pre-expiry curves need a pricing model and IV (v2) |

**Prerequisite:** land the in-progress auth/rate-limit work on the current
branch first (`app.py`, `teasel/rate_limit.py`, `deploy/`, tests are
uncommitted). Build options on top of a clean, deployed baseline.

**Escape hatch:** access is per subdomain. If a paid options tier is ever
wanted (live chains, Greeks), split it into its own tool then. Keeping the
engine in its own module (§2) keeps that split cheap.

---

## 1. Scope

### Build now (MVP)

1. Enter legs manually: long/short call, put, or underlying shares.
2. Strategy presets that fill in legs around a spot price the user enters.
3. Payoff-at-expiry chart with gain/loss fill, strike ticks, breakeven and spot
   markers.
4. Summary: net debit/credit, max profit, max loss (including "unlimited"),
   breakevens, P/L if it expires at the entered spot.
5. Sanity guards (see §3).

### Stub, do not build (v2)

- Pre-expiry P/L curves (Black–Scholes with user-entered IV and date).
- Greeks, P(profit) under a lognormal distribution.
- Multi-expiry positions (calendars, diagonals).
- Early-exercise/assignment modeling.
- Saving positions server-side.

---

## 2. Engine — `teasel/options.py`

Pure standard-library module, like `teasel/engine.py`. Do **not** extend the
ladder engine: the math differs (continuous piecewise-linear payoffs instead of
fixed-payout bins).

### Units

- Strikes, spot, underlying price: `Decimal` dollars.
- Premiums: integer **cents per share** (as quoted).
- Position totals: integer cents where exact (at breakpoints); breakevens are
  `Decimal` dollars, quantized to 4 places.
- `multiplier` defaults to 100 (standard US equity option).

### Model

```python
OptionKind = Literal["CALL", "PUT", "STOCK"]
Direction  = Literal["LONG", "SHORT"]

@dataclass(frozen=True)
class OptionLeg:
    kind: OptionKind
    direction: Direction
    qty: int                      # contracts (or shares for STOCK)
    strike: Decimal | None        # None for STOCK
    premium_cents: int            # per share; entry price for STOCK
    multiplier: int = 100         # forced to 1 for STOCK
    commission_cents: int = 0     # per leg, total
```

Validate in `__post_init__` the same way `Leg` does: strike > 0 for options,
premium ≥ 0, qty ≥ 1, multiplier ≥ 1.

### Algorithm

The payoff at expiry is piecewise linear, with kinks only at strikes. So:

1. `breakpoints = sorted({0} ∪ strikes)`.
2. Evaluate P/L exactly at each breakpoint.
3. The slope beyond the last strike is the sum of long-call qty×mult minus
   short-call qty×mult, plus the net share count.
4. **Max profit/loss:** the maximum or minimum over the breakpoint values, plus
   the tail. A positive upside slope means unlimited profit; a negative one
   means unlimited loss. The downside is always bounded because the price
   can't go below 0.
5. **Breakevens:** for each segment whose endpoints change sign, solve the
   linear equation exactly. Include a breakeven in the upside tail if the
   slope crosses zero there.
6. **Chart vertices:** `[(x_min, pl), *breakpoints_in_range, (x_max, pl)]`.
   The range is `[min_strike, max_strike]` (and spot) padded by 25% or at least
   one strike width, clamped at 0. The server returns the vertices and the
   browser draws straight lines between them. The browser never computes P/L.

### Public API

```python
def analyze_options(legs: Sequence[OptionLeg], spot: Decimal | None,
                    price_range: tuple[Decimal, Decimal] | None = None
                    ) -> OptionsResult
```

`OptionsResult.to_dict()` returns: `net_premium_cents` (signed: + credit,
− debit), `commissions_cents`, `max_profit_cents | "unlimited"`,
`max_loss_cents | "unlimited"`, `breakevens`, `pl_at_spot_cents`, `vertices`,
`strikes`, `guards`.

### Presets — `teasel/options_presets.py`

A pure function `build_preset(name, spot, width, qty) -> list[OptionLeg]`, with
premiums left at 0 for the user to fill in. Presets: long call, long put,
covered call, protective put, bull call spread, bear put spread, straddle,
strangle, iron condor, butterfly.

---

## 3. Guards (the options version of the coherence flags)

These are warnings only. They never block the analysis.

- **Naked short call:** unlimited loss. Point to the leg responsible.
- **Vertical spread priced impossibly:** a debit spread that costs more than
  its strike width, or a credit greater than the width. Usually a typo.
- **Premium below intrinsic value** (only when spot is entered): flags a stale
  or mistyped premium.
- **Zero premium on an option leg:** a reminder to fill it in (matters most
  after applying a preset).
- **Mixed multipliers across legs:** allowed, but called out.

---

## 4. Server — `app.py`

- `GET /options` uses `@auth.login_required()` and renders `options.html`.
- `POST /api/options/analyze` uses `@auth.login_required(api=True)`. The
  existing `restrict_api_origin` check and 64 KiB body cap apply automatically
  because the path starts with `/api/`.
- Rate limiting gets a new bucket, `options_analyze`, set by
  `TEASEL_OPTIONS_ANALYZE_PER_MINUTE` (default 120), so heavy options use can't
  exhaust the ladder's `analyze` budget. Add it to the `RateLimiter` dict.
- Input caps: `MAX_OPTION_LEGS = 16`, qty ≤ 10,000, strike/spot ≤ 1,000,000,
  premium ≤ 10,000,000¢/share. Anything outside the caps returns 422, as the
  ladder route does.
- `GET /api/options/preset?name=…&spot=…&width=…` stays read-only and is
  counted against the same bucket.
- Leave `/api/analyze` and `/api/fetch` unchanged.

---

## 5. Frontend

### Templates

- Move the shared shell (topbar, identity, footer, fonts) into
  `templates/base.html`. `index.html` and a new `options.html` extend it.
- Add a mode switch to the topbar with two tabs, **Kalshi ladders** (`/`) and
  **Options** (`/options`), using plain links.
- Replace the "Snapshot mode" pill with something route-aware: the options page
  has no snapshot, so it shows "Manual entry".
- Page `<title>`: "Teasel · Options Payoff Lab". Rewrite the hero copy to match
  the ladder page's tone.

### JavaScript

- Move the shared helpers (`$`, `apiJson`, `money`, `pct`, `escapeHtml`) out
  of `app.js` into `static/common.js`. **Keep the `app.js` filename:**
  `deploy/rollout_rate_limits.sh` installs and rolls back that exact path.
- Add `static/options.js`:
  - Leg table: kind, long/short, qty, strike, premium, with add/remove rows.
  - Controls: spot input, preset picker, multiplier (advanced, default 100).
  - Debounced `POST /api/options/analyze` on every change, reusing the
    existing stale-request guard (`state.requestId`).
  - Chart: SVG polyline built from `vertices`, gain/loss fill split at the
    zero line, strike ticks, dashed breakeven lines, spot marker. Reuse the
    ladder chart's classes and colors.
  - Metrics card: same layout as "Risk & edge". Show "Unlimited" in red for
    unbounded loss.
- Optional (phase 4): encode legs in the URL hash so a position can be shared
  or bookmarked. It is still behind sign-in and never stored on the server.

### CSS

Reuse `app.css`. Add only the leg-table row controls and the chart markers.

---

## 6. Tests

- **`tests/test_options_engine.py`**: golden cases with hand-computed answers:
  - Long call: max loss = premium, unlimited profit, breakeven = K + premium.
  - Short put: max profit = premium, max loss = K − premium (bounded at 0).
  - Bull call spread: bounded both ways, one breakeven.
  - Iron condor: two breakevens, flat top between the short strikes.
  - Covered call: STOCK + short call, bounded upside, and the naked-call guard
    must **not** fire.
  - Straddle: two breakevens, unlimited upside.
  - Multiplier and commissions: totals scale correctly.
  - Exact breakeven when it lands on a strike, and when the P/L is flat at 0
    over a segment (report the range endpoints and don't duplicate them).
  - Validation failures: negative premium, zero qty, STOCK with a strike.
- **`tests/test_options_presets.py`**: each preset produces the expected leg
  shape.
- **`tests/test_app.py`** additions:
  - Anonymous users can't reach `/options` or `/api/options/analyze` (redirect
    or 401).
  - A free session can use both.
  - Cross-origin POST is rejected.
  - Leg-count and field caps return 422, and an oversized body returns 413.
  - The `options_analyze` bucket is separate from `analyze`.
  - An end-to-end API case matches the engine's golden result.

The ladder tests must keep passing unchanged.

---

## 7. Build order

| Phase | Deliverable | Done when |
|---|---|---|
| 0 | Land the in-progress auth/rate-limit work | Committed, deployed, smoke-checked |
| 1 | `teasel/options.py` + engine tests | All golden cases green, no Flask import |
| 2 | Presets + guards + their tests | Each preset/guard covered |
| 3 | API route, caps, rate bucket, app tests | `test_app` green, curl works locally |
| 4 | `base.html` split, `common.js`/`ladder.js` refactor | Ladder page pixel-identical, tests green |
| 5 | `options.html` + `options.js` + chart | Manual QA on desktop and 375px width |
| 6 | Docs + marketing copy + deploy | See §8 |

Phase 4 is a pure refactor. Ship it separately so a regression on the ladder
page can't hide in the options diff.

---

## 8. Rollout

- **README:** add the new route, the `TEASEL_OPTIONS_ANALYZE_PER_MINUTE`
  variable, and the caps. Add the variable to all three `.env*.example` files
  (it's optional, with a default).
- **Deploy:** same service, same Gunicorn workers. No new secrets and no new
  hosts. Nothing changes in arboretum-accounts.
- **Monitoring:** add an authenticated smoke check of
  `POST /api/options/analyze` to the post-deploy checklist. `/healthz` stays
  as it is.
- **Cloudflare:** the "Free plan, no host-scoped rule" note still applies. If
  an edge rule is added later, include `/api/options/analyze`.
- **Marketing site (`ArboretumInvestmentsWebsite/public/tools.html`):**
  reposition Teasel from "Scalar-ladder payoff analysis for Kalshi events" to
  payoff analysis for Kalshi ladders and options. Update the Free plan line
  ("Size up any Kalshi position…"). House style: no em dashes.
- **Disclosure:** the options page footer says payoffs are at expiration only,
  ignore early assignment, and use prices the user entered. Check that
  `/risk` and `/disclosure` already cover options, and add a line if not.

---

## 9. Open questions

1. Include STOCK legs in the MVP (needed for covered calls and protective
   puts)? **Recommended: yes.** It's a small addition to the engine.
2. Show commissions in the MVP or hide them behind "advanced"? Recommended:
   advanced, default 0.
3. Ship shareable URL-hash positions at launch or later? Recommended: later.
4. Final positioning line for Teasel on the tools page.
