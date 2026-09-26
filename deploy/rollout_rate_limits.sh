#!/usr/bin/env bash
# Run as root on the consolidated host after verifying the staged artifact.
set -euo pipefail

stage="${1:?staged Teasel directory required}"
app_dir=/opt/teasel-fixed
env_file=/etc/arboretum/teasel.env
override=/etc/systemd/system/teasel.service.d/rate-limit-override.conf
backup="/data/teasel/releases/$(date -u +%Y%m%dT%H%M%SZ)"

install -d -m 0750 -o teasel -g teasel /data/teasel
install -d -m 0700 "$backup"
cp -a "$app_dir/app.py" "$backup/app.py"
cp -a "$app_dir/static/app.js" "$backup/app.js"
cp -a "$env_file" "$backup/teasel.env"
had_module=0
had_override=0
if [[ -f "$app_dir/teasel/rate_limit.py" ]]; then
  had_module=1
  cp -a "$app_dir/teasel/rate_limit.py" "$backup/rate_limit.py"
fi
if [[ -f "$override" ]]; then
  had_override=1
  cp -a "$override" "$backup/rate-limit-override.conf"
fi

rollback() {
  trap - ERR
  cp -a "$backup/app.py" "$app_dir/app.py"
  cp -a "$backup/app.js" "$app_dir/static/app.js"
  cp -a "$backup/teasel.env" "$env_file"
  if [[ "$had_module" -eq 1 ]]; then
    cp -a "$backup/rate_limit.py" "$app_dir/teasel/rate_limit.py"
  else
    rm -f "$app_dir/teasel/rate_limit.py"
  fi
  if [[ "$had_override" -eq 1 ]]; then
    cp -a "$backup/rate-limit-override.conf" "$override"
  else
    rm -f "$override"
  fi
  systemctl daemon-reload
  systemctl restart teasel.service
  echo "Teasel rollout failed; restored $backup" >&2
}
trap rollback ERR

install -m 0644 -o teasel -g teasel "$stage/app.py" "$app_dir/app.py"
install -m 0644 -o teasel -g teasel "$stage/static/app.js" "$app_dir/static/app.js"
install -m 0644 -o teasel -g teasel "$stage/teasel/rate_limit.py" "$app_dir/teasel/rate_limit.py"
install -d -m 0755 /etc/systemd/system/teasel.service.d
install -m 0644 "$stage/deploy/rate-limit-override.conf" "$override"

if grep -q '^TEASEL_RATE_LIMIT_DB=' "$env_file"; then
  grep -Fxq 'TEASEL_RATE_LIMIT_DB=/data/teasel/rate_limits.sqlite3' "$env_file"
else
  printf '\nTEASEL_RATE_LIMIT_DB=/data/teasel/rate_limits.sqlite3\n' >>"$env_file"
fi

systemctl daemon-reload
systemctl restart teasel.service
healthy=0
for _attempt in {1..20}; do
  if systemctl is-active --quiet teasel.service && \
    curl -fsS --max-time 2 -H 'Host: teasel.arboretuminvestments.net' \
      http://127.0.0.1:5050/healthz 2>/dev/null | grep -q '"status":"ok"'; then
    healthy=1
    break
  fi
  sleep 0.5
done
test "$healthy" -eq 1
status="$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' \
  -H 'Host: teasel.arboretuminvestments.net' -H 'Content-Type: application/json' \
  --data '{}' http://127.0.0.1:5050/api/fetch)"
test "$status" = 401
test -f /data/teasel/rate_limits.sqlite3

trap - ERR
echo "Teasel rate limits deployed; rollback snapshot: $backup"
