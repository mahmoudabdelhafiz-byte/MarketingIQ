#!/usr/bin/env bash
# Read-only inspection of the shared-hosting account (prefixed with common.sh by the workflow).
# Changes nothing on the server and never prints secret values.
set -u

report=()
warnings=()
add() { report+=("$*"); }
warn() { warnings+=("$*"); }

add "Connected as $(whoami) on $(hostname)"
for tool in git python3 mysqldump curl cloudlinux-selector; do
  command -v "$tool" >/dev/null 2>&1 || warn "Tool $tool: missing"
done

if [ -d "$APP_ROOT" ]; then
  add "App root: $APP_ROOT"
  for item in marketingiq_wsgi.py alembic.ini migrations pyproject.toml; do
    [ -e "$APP_ROOT/$item" ] || warn "App root is missing $item"
  done
else
  warn "App root $APP_ROOT does not exist"
fi

if is_linked; then
  dirty=$(appgit status --porcelain --untracked-files=no | wc -l | tr -d ' ')
  add "Deployed commit: $(appgit log -1 --format='%h %cs %s')"
  [ "$dirty" = "0" ] || warn "$dirty tracked file(s) were changed on the server since the last deploy"
else
  warn "App folder is not linked to GitHub yet: the first deploy must use mode bootstrap-deploy"
fi

if [ -n "${APP_VENV:-}" ] && [ -f "$APP_VENV" ]; then
  add "Virtualenv: $APP_VENV"
else
  warn "cPanel Python 3.12 virtualenv not found; set the APP_VENV variable"
fi

names=$(cpanel_env_names)
if [ -n "$names" ]; then
  add "cPanel settings (names only): $names"
else
  warn "Could not read the cPanel app settings for $REL_ROOT"
fi

if [ -n "$names" ] && [ -n "${APP_VENV:-}" ] && [ -f "$APP_VENV" ]; then
  db_report=$(
    load_cpanel_env
    # shellcheck disable=SC1090
    . "$APP_VENV"
    cd "$APP_ROOT" || exit 0
    echo "Installed package: $(python -m pip show marketingiq 2>/dev/null | awk '/^Version:/{print $2}')"
    python - <<'PY' 2>&1 | tail -n 1
import filecmp
import pathlib

import marketingiq

installed = pathlib.Path(marketingiq.__file__).resolve().parent
source = pathlib.Path("src/marketingiq").resolve()
if installed == source:
    print("Installed code: editable install of the app folder")
else:
    files = [p.relative_to(source) for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    stale = [str(f) for f in files if not (installed / f).is_file() or not filecmp.cmp(source / f, installed / f, shallow=False)]
    print(f"Installed code: {len(files) - len(stale)}/{len(files)} files match the app folder" + (f"; differ: {' '.join(stale[:8])}" if stale else ""))
PY
    python - <<'PY' 2>&1 | tail -n 2
from sqlalchemy import text

from marketingiq.infrastructure.database import create_database_engine
from marketingiq.infrastructure.schema_status import inspect_database_schema

engine = create_database_engine()
with engine.connect() as connection:
    print(f"Database: {connection.scalar(text('SELECT VERSION()'))}")
status = inspect_database_schema(engine)
print(
    f"Schema: {status.state} (database {', '.join(status.current_heads) or 'none'}, "
    f"code expects {', '.join(status.expected_heads) or 'none'})"
)
PY
  )
  while IFS= read -r line; do [ -n "$line" ] && add "$line"; done <<< "$db_report"
fi

# Public exposure: server-only files in a folder that may double as the document root.
public_url="${APP_URL:-}"
if [ -z "$public_url" ] && [ -n "$names" ]; then
  public_url=$(load_cpanel_env && printf '%s' "${MARKETINGIQ_PUBLIC_URL:-}")
fi
if [ -n "$public_url" ]; then
  public_url="${public_url%/}"
  add "Health: /health/live $(http_code "$public_url/health/live"), /health/ready $(http_code "$public_url/health/ready")"
  exposed=()
  for path in .git/HEAD .htaccess.before-hardening stderr.log wsgi_probe.py \
      "MarketingIQ-main.zip" "MarketingIQ-main/README.md" alembic.ini; do
    encoded=${path// /%20}
    code=$(http_code "$public_url/$encoded")
    [ "$code" = "200" ] && exposed+=("$path")
  done
  if [ "${#exposed[@]}" -gt 0 ]; then
    warn "Publicly downloadable from the website: ${exposed[*]}"
  else
    add "Public exposure check: none of the probed server files are downloadable"
  fi
fi

annotate notice "Server check" "${report[@]}"
if [ "${#warnings[@]}" -gt 0 ]; then
  annotate warning "Needs attention" "${warnings[@]}"
fi
printf '%s\n' "${report[@]}"
[ "${#warnings[@]}" -eq 0 ] || printf 'WARNING: %s\n' "${warnings[@]}"
exit 0
