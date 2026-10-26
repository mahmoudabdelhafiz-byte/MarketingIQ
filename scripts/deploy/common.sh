# Shared helpers for the scripts the Deploy workflow runs over SSH.
# The workflow prepends this file to remote_check.sh / remote_deploy.sh and pipes them to bash.
# Nothing here prints secret values.

if [ -z "${APP_ROOT:-}" ]; then
  # cPanel names the application's virtualenv after its application root folder.
  _venv_dir=$(ls -d "$HOME"/virtualenv/*/3.12* 2>/dev/null | head -n 1)
  APP_ROOT="$HOME/$(basename "$(dirname "${_venv_dir:-$HOME/virtualenv/marketingiq/x}")")"
fi
REL_ROOT="${APP_ROOT#"$HOME"/}"
STATE_DIR="$HOME/.marketingiq"
BACKUP_DIR="${MARKETINGIQ_BACKUP_DIR:-$STATE_DIR/backups}"
# The git metadata lives outside the application folder, so it can never be served by the web
# server even when the application folder is also the domain's document root.
APP_GIT_DIR="${MARKETINGIQ_GIT_DIR:-$STATE_DIR/app.git}"
REPO_URL="${MARKETINGIQ_REPO_URL:-https://github.com/mahmoudabdelhafiz-byte/MarketingIQ.git}"
if [ -z "${APP_VENV:-}" ]; then
  APP_VENV=$(ls -d "$HOME/virtualenv/$REL_ROOT"/3.12*/bin/activate 2>/dev/null | head -n 1)
fi

appgit() { git --git-dir="$APP_GIT_DIR" --work-tree="$APP_ROOT" "$@"; }
is_linked() { [ -d "$APP_GIT_DIR" ] && appgit rev-parse --verify -q HEAD >/dev/null 2>&1; }

# Production settings saved in cPanel "Setup Python App" (read via cloudlinux-selector).
CPANEL_ENV_PY='
import json, shlex, subprocess, sys
mode, app_root = sys.argv[1], sys.argv[2]
raw = subprocess.run(["cloudlinux-selector", "get", "--json", "--interpreter", "python"],
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     universal_newlines=True).stdout
env = None
for version in (json.loads(raw or "{}").get("available_versions") or {}).values():
    for user in ((version or {}).get("users") or {}).values():
        app = ((user or {}).get("applications") or {}).get(app_root)
        if app is not None:
            env = app.get("env_vars") or {}
if env is None:
    sys.exit(3)
for key in sorted(env):
    print(key if mode == "names" else "export %s=%s" % (key, shlex.quote(str(env[key]))))
'
cpanel_env_names() { python3 -c "$CPANEL_ENV_PY" names "$REL_ROOT" 2>/dev/null | tr '\n' ' '; }
load_cpanel_env() {
  local exports
  exports=$(python3 -c "$CPANEL_ENV_PY" exports "$REL_ROOT" 2>/dev/null) || return 1
  set -a
  eval "$exports"
  set +a
}

annotate() {
  # annotate LEVEL TITLE LINE... -> one GitHub annotation with the lines joined.
  local level="$1" title="$2"
  shift 2
  local message
  message=$(printf '%s\n' "$@" | sed ':a;N;$!ba;s/%/%25/g;s/\r//g;s/\n/%0A/g')
  echo "::${level} title=${title}::${message}"
}

http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$1" 2>/dev/null || echo 000; }
