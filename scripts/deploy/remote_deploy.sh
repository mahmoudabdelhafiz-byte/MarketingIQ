#!/usr/bin/env bash
# Deploy MarketingIQ on cPanel/Passenger shared hosting. Run over SSH by the Deploy workflow.
#
# Steps: fast-forward the checkout -> install -> preflight -> database backup -> migrate ->
# verify schema -> restart Passenger. Any failure before the restart restores the previous
# commit and package, so the running site keeps serving the old version.
set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-$HOME/marketingiq}"
APP_VENV="${APP_VENV:-}"
DEPLOY_REF="${DEPLOY_REF:-main}"
ENV_FILE="${MARKETINGIQ_ENV_FILE:-$HOME/.marketingiq/env}"
BACKUP_DIR="${MARKETINGIQ_BACKUP_DIR:-$HOME/.marketingiq/backups}"
BACKUP_KEEP="${MARKETINGIQ_BACKUP_KEEP:-10}"

step() { echo "==> $*"; }
fail() { echo "::error title=Deploy failed::$*"; exit 1; }

[ -d "$APP_ROOT" ] || fail "App root $APP_ROOT does not exist"
git -C "$APP_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || fail "$APP_ROOT is not a git checkout"
[ -f "$ENV_FILE" ] || fail "Env file $ENV_FILE not found"
if [ -z "$APP_VENV" ]; then
  APP_VENV=$(ls -d "$HOME"/virtualenv/*/3.12*/bin/activate 2>/dev/null | head -n 1)
fi
[ -n "$APP_VENV" ] && [ -f "$APP_VENV" ] || fail "Python virtualenv not found; set APP_VENV"

# Production settings for CLI steps (Passenger has its own copy in cPanel's app settings).
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
export MARKETINGIQ_PROJECT_ROOT="$APP_ROOT"
# shellcheck disable=SC1090
. "$APP_VENV"
cd "$APP_ROOT"

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  fail "Tracked files were modified on the server; commit or discard them first"
fi

PREVIOUS=$(git rev-parse HEAD)
step "Fetching $DEPLOY_REF (currently at ${PREVIOUS:0:7})"
git fetch --quiet origin "$DEPLOY_REF"
TARGET=$(git rev-parse FETCH_HEAD)
if [ "$PREVIOUS" = "$TARGET" ] && [ "${FORCE_DEPLOY:-false}" != "true" ]; then
  echo "::notice title=Deploy::Already at ${TARGET:0:7}; nothing to deploy"
  exit 0
fi

rollback() {
  echo "::error title=Deploy failed::Rolling back to ${PREVIOUS:0:7}; the site was not restarted"
  git reset --quiet --hard "$PREVIOUS"
  python -m pip install --quiet . || echo "::warning::Reinstalling the previous package failed"
}
trap rollback ERR

step "Updating code to ${TARGET:0:7}"
if git symbolic-ref --quiet HEAD >/dev/null; then
  git merge --quiet --ff-only "$TARGET" || {
    trap - ERR
    fail "Server branch cannot fast-forward to $DEPLOY_REF"
  }
else
  git checkout --quiet "$TARGET"
fi

step "Installing package"
python -m pip install --quiet .

step "Validating production configuration"
python -m marketingiq.jobs.validate_deployment >/dev/null

step "Backing up database"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
eval "$(python - <<'PY'
import os
import shlex

from sqlalchemy.engine import make_url

url = make_url(os.environ["DATABASE_URL"])
for key, value in {
    "DB_HOST": url.host or "localhost",
    "DB_PORT": str(url.port or 3306),
    "DB_USER": url.username or "",
    "MYSQL_PWD": url.password or "",
    "DB_NAME": url.database or "",
}.items():
    print(f"export {key}={shlex.quote(value)}")
PY
)"
BACKUP_FILE="$BACKUP_DIR/marketingiq-$(date -u +%Y%m%dT%H%M%SZ)-${PREVIOUS:0:7}.sql.gz"
mysqldump --single-transaction --no-tablespaces --routines \
  -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" | gzip > "$BACKUP_FILE"
unset MYSQL_PWD
chmod 600 "$BACKUP_FILE"
ls -1t "$BACKUP_DIR"/marketingiq-*.sql.gz | tail -n +"$((BACKUP_KEEP + 1))" | xargs -r rm -f
echo "Backup: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

step "Migrating database"
alembic upgrade head

step "Verifying schema"
python -m marketingiq.jobs.verify_database_schema >/dev/null

trap - ERR
step "Restarting Passenger"
mkdir -p tmp
touch tmp/restart.txt

echo "::notice title=Deploy::Deployed ${TARGET:0:7} (was ${PREVIOUS:0:7}); backup $(basename "$BACKUP_FILE")"
