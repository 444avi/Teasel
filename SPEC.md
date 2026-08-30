# Kalshi Scalar-Ladder Payoff Tool — Build Spec

Internal tool for a prediction-market fund. Paste a Kalshi event link, pick a
side (YES/NO) and quantity on any rungs, and get a payoff diagram plus
correctness/EV diagnostics.

> Status: validated, not yet built. This doc supersedes the original build
> brief. Changes from the brief are marked **[revised]** with the reason.

---

## 0. Reuse & provenance **[revised]**

The original brief said to reuse prior work under an `arbmon` project. That
folder (`~/Downloads/arbmon`) was **not recognized as ours** and is **not**
used. This project is greenfield.

Three Kalshi clients exist on the machine and were evaluated:
`~/Downloads/arbmon/arbmon/clients.py` (bulk scan + orderbook, float dollars,
no event fetch, no link parsing), `~/vavi/kalshi.py`, and
`~/avisight/avisight/kalshi.py`. Only avisight has event-link parsing and
event-ladder fetch, so it is the chosen base.

**Reuse target (confirmed):** `avisight/kalshi.py`.

What we take from it (read-only Kalshi client, cents/`Decimal`, verified against
the live API 2026-08-14):

- `parse_event_ticker(text)` — pasted kalshi.com link → event ticker.
- `KalshiClient.get_event_markets(event_ticker)` — every rung under an event,
  cursor-paged.
- `get_orderbook_bid_levels`, `extract_bid_cents`, `extract_bid_size` —
  version-tolerant handling of the current `_dollars` / `_fp` field shapes.
- The `yes_ask = 100 − no_bid` identity is already implicit in its bid model,
  which is exactly the "never price entry off the bid" rule below.

**Net-new even with avisight** (the reuse does NOT cover these — build them):

1. **Fee model.** avisight has none. Build fresh (Kalshi's published formula,
   see §5).
2. **Threshold extraction.** avisight *names* rungs but does not produce a
   numeric strike. Parse Kalshi strike fields (`floor_strike` / `cap_strike` /
   `custom_strike`) or the "Above $T" text into a number (see §3).
3. **The entire payoff / EV / guards engine** — the core deliverable.

---

## 1. Scope

### Build now (MVP)

1. Ingest a scalar ladder from a Kalshi event link/ticker.
2. Select legs (side + qty per rung); show the position's net cost.
3. Compute payoff over the ladder's discrete outcome bins; render a
   step-function payoff diagram.
4. Run coherence/arbitrage guards; show market-implied EV as a diagnostic only.

### Stub interfaces, DO NOT build (v2)

- EV against the fund's own (subjective) probabilities.
- Per-bin / distribution-fit probability input.
- Position sizing beyond reporting capital-at-risk.
- Live websocket price updates (MVP is snapshot-on-fetch).

### Architecture rule

The payoff/EV engine is a **pure, framework-free, fully-tested Python module**,
separate from any server and UI. All math is server-side and is the single
source of truth; the frontend only renders.

---

## 2. Build order **[revised — engine first]**

1. **Pure engine + full test suite.** Framework-free, importable without a
   server. Bin algebra, pricing, fee model, diagnostics, guards, golden tests.
2. **Ingest adapter** on top of `avisight`'s client (event fetch + threshold
   extraction → canonical bins).
3. **UI (deferred).** Flask-style backend + payoff chart. Stack chosen at this
   step, not now (see §8).

---

## 3. Domain model — implement precisely

### Scalar ladder → exclusive bins

Kalshi presents ladders as cumulative "Above $T" rungs. Given selected
thresholds sorted `t1 < t2 < … < tn`, define `n+1` half-open bins:

```
(-inf, t1], (t1, t2], …, (tn-1, tn], (tn, +inf)
```

The first and last are open-ended tails — label them `≤ t1` and `≥ tn` and
never draw a fake outer edge.

Support both cumulative ("Above $T") and bracket ("$A–$B") inputs by converting
on ingest: first-difference cumulative probabilities to get bin probabilities;
cumulative-sum bins to get thresholds. **Canonical internal representation is
the exclusive-bin partition.**

### Threshold extraction **[revised — net-new]**

avisight does not yield a numeric strike. On ingest, derive each rung's
threshold from Kalshi's strike fields (`floor_strike` / `cap_strike` /
`custom_strike`) when present, falling back to parsing the "Above $T" subtitle.
Confirm the rung's boundary operator (see boundary note in §4) rather than
assuming.

### Leg

```
{ market_id, threshold, side: YES|NO, qty, entry_price_cents, fee_cents }
```

### Units **[revised]**

The engine works in **integer cents internally** (contracts settle at exactly
100¢). This matches avisight's cents/`Decimal` client, keeps the "comb"
payoff equalities exact, and avoids float drift in golden tests. Convert to
dollars only at the reporting boundary.

---

## 4. Payoff matrix

`M[bin][leg] ∈ {0,1}`.

- A **YES** leg on threshold `tk` pays $1 in every bin **strictly above** `tk`
  (bins `k+1 … n+1`).
- A **NO** leg on `tk` pays $1 in every bin **at or below** `tk` (bins
  `1 … k`).

Each contract settles at exactly 100¢.

```
net_payoff_per_bin = Σ_leg M[bin][leg] · qty · 100¢ − total_cost_cents
```

reported in dollars.

**Boundary note.** The bins put `tk` in the *below/NO* bin (`(-inf, t1]`), i.e.
"Above $T" is treated as strict (`>`). This is correct for the `.99` strike
convention (an outcome never lands exactly on `82.99`). Confirm the real strike
operator on ingest; a rung expressed as `≥` would flip one boundary.

---

## 5. Pricing — the #1 correctness trap

- **Entry cost is the ask** (we cross the spread to enter). Buy YES → pay
  `yes_ask`; buy NO → pay `no_ask`.
- If the source exposes only bids, **derive the ask**: `yes_ask ≈ 100 − no_bid`
  (same order book). **Never price entry off the bid.** avisight exposes bids;
  use `extract_bid_cents(m, "no")` to get `yes_ask`, and vice versa.
- Add a per-leg fee via a **pluggable fee function**.
- `total_cost_cents = Σ (entry_ask + fee) · qty`.

### Fee model **[revised — net-new, was assumed reused]**

avisight has no fee model; build it fresh. Kalshi's taker fee is
price-dependent and peaks near 50¢ (NOT a flat haircut):

```
fee_cents(price, qty) = ceil_to_cent( rate · qty · price · (1 − price) )
```

- `rate` default `0.07` (7%), configurable per market.
- Maker/resting orders are free → a `use_maker` flag zeroes the fee.
- Implemented in cents; `price` is the executable ask.

### Market-implied bin probabilities

First differences of `P(above t_k)`. Source is **explicit and configurable**:
the market's displayed chance, or the mid `(yes_bid + yes_ask)/2`. Deriving
probs from the *ask* would make market-EV self-referentially ≈0 and mask the
signal — prefer mid or displayed chance.

---

## 6. Diagnostics & guards (first-class outputs, each tested)

- **Monotonicity guard.** `P(above t)` must be non-increasing as the strike
  rises. A higher strike priced richer than a lower one implies a negative bin
  probability — flag as a stale/incoherent quote and **refuse to feed it into
  EV.** Never silently pass it through.

- **Arbitrage / coherence check.** Flag when a complete leg set costs less than
  its guaranteed minimum payout (risk-free profit). Compute
  `market_EV = Σ_bin p_bin · net_payoff_bin`. In a coherent ladder this is
  `≤ 0` (you pay the spread/vig), by linearity of per-leg EV under the implied
  probs. Treat `market_EV > 0` as an **arbitrage ALARM**, not a "good trade"
  signal — label it in the UI so it never reads as the primary buy signal.

- **True capital at risk.** Some positions guarantee a partial payout:
  `max_loss = total_cost − min(gross_payoff_over_bins)`, which can be far less
  than the full outlay. Report `max_loss`, `max_gain`, breakeven bins, and
  `P(profit)` under market probabilities. Any future sizing uses `max_loss`,
  not gross cost.

---

## 7. Tests

### Acceptance test — pinned **[revised: fee handling made explicit]**

**Event:** Oil Price (WTI) tomorrow?

**Position:** NO Above $82.99 @ 26¢, YES Above $83.99 @ 65¢,
NO Above $84.99 @ 67¢, YES Above $85.99 @ 15¢. Thresholds carve 5 bins.

Thresholds `t1..t4 = 82.99, 83.99, 84.99, 85.99` give 5 bins (low→high):

```
B1 = ≤ 82.99   B2 = (82.99, 83.99]   B3 = (83.99, 84.99]
B4 = (84.99, 85.99]   B5 = ≥ 85.99
```

**Leg → bins paid** (YES on tk pays bins above tk; NO on tk pays bins ≤ tk):

| Leg              | k | Pays in         |
|------------------|---|-----------------|
| NO  Above 82.99  | 1 | B1              |
| YES Above 83.99  | 2 | B3, B4, B5      |
| NO  Above 84.99  | 3 | B1, B2, B3      |
| YES Above 85.99  | 4 | B5              |

Summing gives gross payoff per bin = 2, 1, 2, 1, 2.

Market-EV arithmetic (bin probs 25/11/28/19/17, net per bin in $):

```
EV = 0.25(+0.27) + 0.11(−0.73) + 0.28(+0.27) + 0.19(−0.73) + 0.17(+0.27)
   = −0.0300
P(profit) = P(net>0) = bins B1,B3,B5 = 25 + 28 + 17 = 70%
```

> The pinned figures below hold at **zero fees**. Run this test with the fee
> model disabled (`use_maker=True` or `rate=0`) so it is deterministic and
> decoupled from the fee assumption. **A separate test pins the fee
> contribution** (see below). Do not leave the flagship test implicitly
> fee-free while the code always applies fees — that contradiction is the main
> correction from validation.

Expected (fee-free):

- Net cost = **$1.73**.
- Gross payoff per bin (low→high) = **2, 1, 2, 1, 2**; net =
  **+0.27, −0.73, +0.27, −0.73, +0.27** ("comb").
- `min gross = $1.00` ⇒ capital at risk = **$0.73** (not $1.73);
  `max_gain = $0.27`.
- With market bin probs **25/11/28/19/17**, market-EV ≈ **−$0.03** and
  P(profit) = **70%**.

### Fee-pinning test **[revised — new]**

Same four legs, fee model **on** at `rate=0.07`, qty 1:

- Per-leg fee = `ceil_to_cent(0.07 · price · (1−price))` →
  2¢, 2¢, 2¢, 1¢ → **total fees = 7¢**.
- Total cost = **$1.80**; capital at risk = **$0.80**; `max_gain = $0.20`.

### Golden tests

- A monotone "ramp" (stacked YES) → a linear staircase payoff.
- A deliberately inverted ladder → **must raise the monotonicity flag**.
- A leg set that **must raise the arbitrage flag**.

These guard the bin algebra against silent breakage on refactor.

---

## 8. UI **[deferred — stack TBD]**

Chosen at the UI build step, not now. Candidates:

- **Flask + Plotly** (`line_shape='hv'`) — fastest to the open-tail step
  diagram.
- **Flask + hand-built SVG/D3** — more control over tail labels/styling.

Chart.js/Recharts are avoided — they fight the open tails.

Planned UX: paste event link → fetch → ladder table sorted by strike (columns:
threshold, YES ask, NO ask, P(above)); per rung pick YES/NO + qty, with
contiguous-range selection in one gesture. Render (a) the step-function payoff
diagram with open-ended tail labels, a dashed breakeven line, and gain/loss
coloring; (b) a results panel with net cost, capital-at-risk, max loss/max
gain, P(profit), the market-EV diagnostic, and the guard flags.

---

## 9. Working style

Commit in logical steps (engine + tests first, then ingest adapter, then UI).
Keep the engine importable and testable without a server. Where a decision is
ambiguous and cheap to reverse, proceed and note the assumption in the commit
message; where it is structural (data source, framework), ask first.
