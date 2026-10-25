#!/usr/bin/env bash
# Read-only inspection of the shared-hosting account, run over SSH by the Deploy workflow.
# It changes nothing on the server and never prints secret values.
set -u

if [ -z "${APP_ROOT:-}" ]; then
  # cPanel names the virtualenv after the application root folder.
  venv_dir=$(ls -d "$HOME"/virtualenv/*/3.12* 2>/dev/null | head -n 1)
  APP_ROOT="$HOME/$(basename "$(dirname "${venv_dir:-$HOME/virtualenv/marketingiq/x}")")"
fi
APP_VENV="${APP_VENV:-}"
ENV_FILE="${MARKETINGIQ_ENV_FILE:-$HOME/.marketingiq/env}"
report=()
warnings=()
add() { report+=("$*"); }
warn() { warnings+=("$*"); }

add "Connected as $(whoami) on $(hostname)"
add "Home directory: $HOME"
for tool in git python3 mysqldump curl; do
  if command -v "$tool" >/dev/null 2>&1; then add "Tool $tool: available"; else warn "Tool $tool: missing"; fi
done

if [ -d "$APP_ROOT" ]; then
  add "App root: $APP_ROOT"
  for item in marketingiq_wsgi.py alembic.ini migrations pyproject.toml; do
    [ -e "$APP_ROOT/$item" ] || warn "App root is missing $item"
  done
  if git -C "$APP_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    branch=$(git -C "$APP_ROOT" rev-parse --abbrev-ref HEAD)
    commit=$(git -C "$APP_ROOT" log -1 --format='%h %cs %s')
    remote=$(git -C "$APP_ROOT" remote get-url origin 2>/dev/null | sed -E 's#//[^@/]*@#//#')
    dirty=$(git -C "$APP_ROOT" status --porcelain --untracked-files=no | wc -l | tr -d ' ')
    add "Git checkout: branch $branch at $commit"
    add "Git remote: ${remote:-none}"
    if [ "$dirty" != "0" ]; then warn "Git checkout has $dirty locally modified tracked file(s)"; fi
  else
    warn "App root is not a git checkout (deploy will need a one-time git setup)"
  fi
else
  warn "App root $APP_ROOT does not exist"
  found=$(find "$HOME" -maxdepth 3 -name marketingiq_wsgi.py -not -path '*/virtualenv/*' 2>/dev/null \
    | xargs -r -n1 dirname | tr '\n' ' ')
  add "Folders containing marketingiq_wsgi.py: ${found:-none}"
  for dir in $found; do
    if git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      add "  $dir is a git checkout at $(git -C "$dir" log -1 --format='%h %cs') on $(git -C "$dir" rev-parse --abbrev-ref HEAD), remote $(git -C "$dir" remote get-url origin 2>/dev/null | sed -E 's#//[^@/]*@#//#')"
    else
      add "  $dir is not a git checkout; files dated $(stat -c '%y' "$dir/marketingiq_wsgi.py" | cut -d. -f1)"
    fi
    [ -f "$dir/passenger_wsgi.py" ] && add "  $dir has cPanel passenger_wsgi.py"
  done
fi

if [ -z "$APP_VENV" ]; then
  APP_VENV=$(ls -d "$HOME"/virtualenv/*/3.12*/bin/activate 2>/dev/null | head -n 1)
  [ -n "$APP_VENV" ] && add "Detected venv (set APP_VENV to confirm): $APP_VENV"
fi
if [ -n "$APP_VENV" ] && [ -f "$APP_VENV" ]; then
  # shellcheck disable=SC1090
  . "$APP_VENV"
  add "Venv python: $(python --version 2>&1)"
  installed=$(python -m pip show marketingiq 2>/dev/null | awk '/^Version:/{print $2}')
  add "Installed marketingiq package: ${installed:-not installed}"
  deactivate >/dev/null 2>&1 || true
else
  warn "No cPanel Python 3.12 virtualenv found; candidates: $(ls -d "$HOME"/virtualenv/*/*/bin/activate 2>/dev/null | tr '\n' ' ')"
fi

if [ -f "$ENV_FILE" ]; then
  perms=$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '?')
  names=$(grep -Eo '^(export[[:space:]]+)?[A-Z_][A-Z0-9_]*=' "$ENV_FILE" | sed -E 's/^export[[:space:]]+//; s/=$//' | sort | tr '\n' ' ')
  add "Env file $ENV_FILE present (mode $perms); variable names: $names"
  [ "$perms" = "600" ] || [ "$perms" = "400" ] || warn "Env file should be chmod 600"
else
  warn "Env file $ENV_FILE not found (needed for migrations over SSH)"
fi

if command -v cloudlinux-selector >/dev/null 2>&1; then
  rel_root="${APP_ROOT#"$HOME"/}"
  names=$(cloudlinux-selector get --json --interpreter python --app-root "$rel_root" 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(" ".join(sorted((d.get("data") or d).get("env_vars") or {})))' 2>/dev/null)
  if [ -n "$names" ]; then
    add "cPanel app env var names (values not shown): $names"
  else
    warn "cloudlinux-selector present but app env vars could not be read for $rel_root"
    shape=$(cloudlinux-selector get --json --interpreter python 2>&1 | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    data = json.loads(raw)
except ValueError:
    print("non-JSON output, first 120 chars: " + raw[:120].replace("\n", " "))
    raise SystemExit
def walk(node, path, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env_vars" and isinstance(value, dict):
                out.append(path + ".env_vars{" + ",".join(sorted(value)) + "}")
            else:
                walk(value, path + "." + str(key), out)
    elif isinstance(node, list):
        for item in node[:3]:
            walk(item, path + "[]", out)
    else:
        out.append(path)
out = []
walk(data, "", out)
print(" ".join(sorted(set(out)))[:1500])
' 2>&1)
    add "cloudlinux-selector shape: $shape"
  fi
else
  warn "cloudlinux-selector not available"
fi

[ -d "$APP_ROOT/tmp" ] && add "Passenger restart dir: $APP_ROOT/tmp" || warn "No $APP_ROOT/tmp yet (deploy creates it)"

# GitHub annotations: one notice for the report, one warning for anything that needs attention.
encode() { local IFS=$'\n'; printf '%s' "$*" | sed ':a;N;$!ba;s/%/%25/g;s/\n/%0A/g'; }
echo "::notice title=Server check::$(encode "${report[@]}")"
if [ "${#warnings[@]}" -gt 0 ]; then
  echo "::warning title=Needs attention::$(encode "${warnings[@]}")"
  printf 'WARNING: %s\n' "${warnings[@]}"
fi
printf '%s\n' "${report[@]}"
