# Options Payoff Page: Rollout Notes

Operator notes for releasing the `/options` page from branch
`feature/options-payoff`. Nothing here has been deployed.

## Deploy the whole tree

`deploy/rollout_rate_limits.sh` only installs and rolls back `app.py`,
`static/app.js` and `teasel/rate_limit.py`. This release adds new modules,
templates and static files, so **that script cannot be used as-is**. Deploy the
whole application tree, then restart the service.

### Added files

| Path | Purpose |
|---|---|
| `teasel/options.py` | Pure options payoff-at-expiration engine |
| `teasel/options_presets.py` | Strategy presets |
| `templates/base.html` | Shared page shell and tool nav |
| `templates/options.html` | Options page |
| `static/common.js` | Helpers shared by both pages |
| `static/options.js` | Options page script |
| `tests/test_options_engine.py`, `tests/test_options_presets.py` | Tests |
| `deploy/OPTIONS_ROLLOUT.md` | This file |

### Changed files

| Path | Change |
|---|---|
| `app.py` | `/options` and `/api/options/*` routes, input caps, `options_analyze` rate bucket |
| `templates/index.html` | Extends `base.html`; ladder content unchanged |
| `static/app.js` | Shared helpers moved to `common.js`; nothing else changed |
| `static/app.css` | Tool nav and options page styles appended |
| `tests/test_app.py` | Options route tests; test `RateLimiter` knows the new bucket |
| `.env.example`, `.env.staging.example`, `.env.production.example` | New optional env var |
| `README.md` | Routes, limits and caps |
| `OPTIONS_PLAN.md` | Status line |

`static/common.js` must be deployed together with `static/app.js`: the ladder
page no longer works with the new `app.js` and without `common.js`.

## Configuration

One new **optional** variable in `/etc/arboretum/teasel.env`:

```
TEASEL_OPTIONS_ANALYZE_PER_MINUTE=120
```

It defaults to 120 when absent and must be a positive integer. It limits
`POST /api/options/analyze` and `POST /api/options/preset` together, per
account, in the existing SQLite counter (`TEASEL_RATE_LIMIT_DB`). No other
configuration, secret or data change is needed.

## Post-deploy smoke check

```bash
# Without a session: 401 JSON with auth_url
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  -H 'Content-Type: application/json' -d '{"legs":[]}' \
  https://teasel.arboretuminvestments.net/api/options/analyze
```

Then, signed in through the browser:

- `/options` loads with the bull call spread example, a breakeven at $104 and
  a spot line at $103 (so `POST /api/options/analyze` returned 200).
- The "Kalshi ladders" tab still loads `/` with the demo ladder.

## Cloudflare

Any future Cloudflare rate rule for Teasel must include
`/api/options/analyze` and `/api/options/preset` alongside `/api/fetch` and
`/api/analyze`, and stay scoped to the `teasel.arboretuminvestments.net` host.

## Rollback

Redeploy the previous tree and restart. There is no data migration; unused
`options_analyze` counter rows are pruned by the next rate-limited request. Leaving
`TEASEL_OPTIONS_ANALYZE_PER_MINUTE` in the env file is harmless for the old
build.

## Proposed marketing copy (for the owner)

Not applied. `/Users/avi/ArboretumInvestmentsWebsite` auto-deploys the live
site when its default branch is pushed, so the owner should make these edits.
House style: no em dashes.

**`public/tools.html`, Teasel band**

- Function line, currently "Scalar-ladder payoff analysis for Kalshi events.":

  > Payoff analysis for Kalshi ladders and options positions.

- Add a paragraph after the first one:

  > Teasel also maps options trades. Enter each call, put or stock leg at the
  > price you would trade, or start from a preset such as a covered call or an
  > iron condor, and see profit and loss at expiration across every price the
  > underlying can settle at, with max profit, max loss and breakevens.

- Meta and Open Graph descriptions: "Teasel for payoff analysis" can stay as
  is; it covers both pages.

**`public/index.html`, Free plan**

- Pitch, currently "Size up any Kalshi position before you put money behind
  it.":

  > Size up any Kalshi ladder or options trade before you put money behind it.

- Tool line, currently "after-fee payoff analysis":

  > after-fee Kalshi payoffs and options payoff maps
