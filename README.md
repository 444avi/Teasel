# Teasel

Teasel analyzes Kalshi scalar-ladder positions. Every page and API route requires a
WorkOS-verified Arboretum account at the Free plan or above. Calculations remain in
`teasel/engine.py`; Teasel has no WorkOS, database, Stripe, or signing-key secret.

## Shared package and local setup

Teasel pins `arboretum-auth[flask]==0.1.2` in `requirements.txt`. The source is
`../arboretum-accounts/packages/arboretum-auth`. Build and publish that exact
version to the Arboretum private Python package index before installing Teasel
in staging or production:

```bash
cd ../arboretum-accounts
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade build
.venv/bin/python -m build --wheel packages/arboretum-auth
# Upload packages/arboretum-auth/dist/arboretum_auth-0.1.2-py3-none-any.whl
# to the private index using the approved release process.

cd ../Teasel
python3 -m venv .venv
PIP_INDEX_URL=https://YOUR_PRIVATE_INDEX/simple .venv/bin/pip install -r requirements.txt
```

The verified 0.1.2 wheel has SHA-256
`4cb1913c3bbc7e6edea249f919475f5fa69873c7ca23c9c79e4ec4269d34f2d3`.
Publish that artifact, verify its digest during release, and do not replace it
with another build of the same version. Resolve dependencies in a deployment
lock or image for repeatable deployments. The private index must also provide
Flask and the package's public dependencies (directly or through a mirror).
For local development before publication,
install `Flask==3.1.2` and the sibling package with
`.venv/bin/pip install -e ../arboretum-accounts/packages/arboretum-auth`.
This local editable path is a development convenience only.

Run the account service from its own README. Then load `.env.example` and start
Teasel:

```bash
set -a; source .env.example; set +a
.venv/bin/python app.py
```

Open `http://127.0.0.1:5050`. The account service must have WorkOS development
credentials for a real email-code login. Its signing key must remain stable while
you test; restarting a development service with an ephemeral key invalidates
existing sessions. Teasel's `create_app()` validates its auth configuration at
startup, and staging/production refuse missing or mismatched settings.
Localhost cookies are scoped by host, not port, so this single-host development
setup may send the account-service refresh cookie to Teasel. Use distinct local
hostnames when testing host-only cookie isolation; staging and production use
separate account and tool subdomains.

## Authentication configuration

The example environment files contain all Teasel settings. `TEASEL_PUBLIC_URL`
is the browser-visible origin; set the reverse proxy to preserve HTTPS and send
traffic for that host. `ARBORETUM_ACCOUNTS_URL`, `ARBORETUM_ISSUER`,
`ARBORETUM_AUDIENCE`, `ARBORETUM_JWKS_URL`,
`ARBORETUM_ALLOWED_RETURN_HOSTS`, and `ARBORETUM_RETURN_ORIGIN` configure the
shared verifier and safe return URL. The account service's issuer and audience
must match exactly. No private key or WorkOS credential belongs in Teasel.

The account service **must** set `ACCOUNT_COOKIE_DOMAIN=.arboretuminvestments.net`
in production, `ACCOUNT_COOKIE_SECURE=true`, and include
`teasel.arboretuminvestments.net` in `ACCOUNT_ALLOWED_RETURN_HOSTS`. The current
account-service `.env.production.example` has those values. Staging uses
`.staging.arboretuminvestments.net` and its matching Teasel host. Verify the
actual deployed cookie `Domain`, `Secure`, `HttpOnly`, and `SameSite=Lax` in a
browser: a host-only `arb_session` will never reach Teasel. The `arb_refresh`
cookie must remain host-only to the account service.

For a WSGI server, use the factory (`gunicorn 'app:create_app()'`). The public
`GET /healthz` endpoint is for monitoring and contains no identity. Every other
application route requires a valid session. Pages go through central `/refresh`
(which falls back to `/login`); APIs return JSON 401 with an `auth_url`. Expired
sessions use the same renewal path. A failed round trip stops with 401 instead
of redirecting forever. Verification outages return 503 without clearing cookies.

## Resource limits

`POST /api/fetch` allows 12 requests per verified account ID per minute;
`POST /api/analyze` allows 120. The fixed-window counter is an atomic SQLite
transaction in `TEASEL_RATE_LIMIT_DB`, so both Gunicorn workers use the same
counts. Set this to an absolute path on the host's writable local data volume
(production example: `/data/teasel/rate_limits.sqlite3`), owned by the Teasel
service user. Do not place it on separate worker filesystems or network storage.
`TEASEL_FETCH_PER_MINUTE` and `TEASEL_ANALYZE_PER_MINUTE` can tune the limits.
An unavailable counter fails closed with JSON 503; exceeding a limit returns
JSON 429 and `Retry-After` seconds. Counters are short-lived and can be removed
after stopping Teasel; they are not account records or a backup target.

Both POST routes cap JSON bodies at 64 KiB. Event input is limited to 256
characters and analysis to 64 rungs. Oversized bodies return JSON 413 and
oversized fields return JSON 422. The browser shows a retry message for 429.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
# From ../arboretum-accounts:
./scripts/run_tests.sh
```

Tests use local RSA test keys and mocked provider calls. They need no network or
real WorkOS email. The existing payoff and Kalshi-ingest tests still run.

## Security and operations

Identity-specific responses are `Cache-Control: private, no-store`; no JWT is
placed in JavaScript storage. The server only accepts POST API requests from its
own browser origin (`Origin` and `Sec-Fetch-Site` checks). `/api/fetch` and
`/api/analyze` do not change account data, but they consume resources. Teasel
enforces per-account limits; an eligible Cloudflare zone can add a host-scoped
IP limit. Do not log `Cookie`, JWTs, WorkOS callback codes, or email OTPs.

At the Cloudflare edge, add an IP-based rule only when the zone plan exposes a
Host match: restrict it to `teasel.arboretuminvestments.net` **and** the POST
`/api/fetch` or `/api/analyze` paths. A path-only rule can affect other tools
under the same zone and must not be used. Cloudflare counters may be per data
center and delayed; the SQLite account limit remains the authoritative origin
control.

As checked on September 17, 2026, `arboretuminvestments.net` has Cloudflare's
Free plan, which does not expose Host matching for rate rules. No edge rule was
installed; revisit this after a plan change and verify the rule expression
before enabling it.

Operational monitoring should check both public `/healthz` endpoints, review
Teasel's `teasel_response status=429|502|503` journal entries, service restart
counts, and the age of the latest completed backup marker. Health checks alone
do not exercise a signed-in session or JWKS verification; include an authenticated
smoke check after each deployment and when verification errors rise.
The active Codex heartbeat, **Arboretum auth and Teasel health**, runs every
30 minutes, checks the public JWKS as well, and alerts on unhealthy results,
502/503 responses, restarts, or a completed backup older than 30 hours. It
uses `curl` for public probes; Python's default urllib client received a
Cloudflare HTTP error during the baseline check despite `curl` receiving 200.
Cloudflare-generated 502s may not appear in the origin journal, so the public
probes can catch them only while they are occurring. The monitor reports when
AWS access needs renewal.

The parent-domain `arb_session` is sent to every Arboretum subdomain. A compromised
or untrusted sibling host can receive that bearer token during its 15-minute
lifetime. Keep untrusted apps off `*.arboretuminvestments.net`, lock down sibling
services, and keep the short token TTL. The account service's host-only refresh
cookie limits renewal exposure. Plan and session revocations still take up to the
access-token TTL to reach tools.

## Staging rollout

1. Build and publish `arboretum-auth` 0.1.2 to the private index, then deploy the
   updated account service first with its staging WorkOS environment, migrations,
   stable signing key, staging callback URL, return-host allowlist, and
   `ACCOUNT_COOKIE_DOMAIN=.staging.arboretuminvestments.net`.
2. Confirm `/healthz` and JWKS on the account host. Complete a real WorkOS
   six-digit email-code login and confirm the account page shows a new Free user.
3. Deploy Teasel staging with `.env.staging.example` values, the pinned package,
   HTTPS, and the trusted host. Check an anonymous page, all anonymous APIs, a
   Free session on every route, expired-session refresh, sign-out, Premium/Max
   access, and 503 behavior during a controlled JWKS outage.
4. Inspect browser cookies and response cache headers. Confirm cross-origin API
   requests fail and the reverse proxy rate limits resource-heavy calls.

## Production cutover and rollback

1. Deploy the updated account service and `arboretum-auth` wheel first. Configure
   the production WorkOS callback, signing key, cookie domain, and Teasel return
   host. Verify account login and refresh on a test account.
2. Deploy Teasel with `.env.production.example` values and run the smoke checks
   above. At customer cutover, remove Cloudflare Access from customer-facing
   Teasel: otherwise customers see two login layers and use Access seats. Keep
   Access on internal/admin systems only.
3. Keep the previous Teasel artifact and proxy configuration available. If the
   new flow fails, restore the prior Teasel artifact **and its previous access
   policy together**, or temporarily stop customer traffic with a maintenance
   page. Do not expose the old anonymous app to the public. Account-service
   cookies and data can remain; investigate before retrying cutover.

No live Cloudflare Access policy, DNS, WorkOS setting, or deployment is changed
by this repository work. Those are manual, authorized rollout steps.
