#!/usr/bin/env bash
# Deploy MarketingIQ on cPanel/Passenger (prefixed with common.sh by the workflow).
#
# update:    hard-reset the linked checkout to DEPLOY_REF
# bootstrap: first deploy of a folder that was uploaded by hand. Snapshots the folder, links it
#            to GitHub (git metadata kept outside the folder) and writes DEPLOY_REF's files.
#            Untracked server files (.htaccess, uploads, logs) are never touched.
# Then: pip install -> preflight -> database backup -> migrate -> verify -> restart -> health.
# A failure before the restart restores the previous code. If the site is unhealthy after the
# restart and no migration ran, the previous code is restored and Passenger restarted again.
set -Eeuo pipefail

DEPLOY_REF="${DEPLOY_REF:-main}"
BOOTSTRAP="${BOOTSTRAP:-false}"
BACKUP_KEEP="${MARKETINGIQ_BACKUP_KEEP:-10}"
TS=$(date -u +%Y%m%dT%H%M%SZ)

step() { echo "==> $*"; }
fail() { annotate error "Deploy failed" "$*"; exit 1; }

[ -d "$APP_ROOT" ] || fail "App root $APP_ROOT does not exist"
[ -n "${APP_VENV:-}" ] && [ -f "$APP_VENV" ] || fail "Python virtualenv not found; set APP_VENV"
load_cpanel_env || fail "Could not read the cPanel app settings for $REL_ROOT"
export MARKETINGIQ_PROJECT_ROOT="$APP_ROOT"
# shellcheck disable=SC1090
. "$APP_VENV"
cd "$APP_ROOT"
mkdir -p "$BACKUP_DIR"
chmod 700 "$STATE_DIR" "$BACKUP_DIR"

MODE=update
PREVIOUS=""
SNAPSHOT=""
if is_linked; then
  if [ -n "$(appgit status --porcelain --untracked-files=no)" ]; then
    fail "Tracked files were changed on the server since the last deploy; nothing was changed"
  fi
  PREVIOUS=$(appgit rev-parse HEAD)
else
  [ "$BOOTSTRAP" = "true" ] || fail "The app folder is not linked to GitHub yet; run mode bootstrap-deploy"
  MODE=bootstrap
fi

if [ "$MODE" = bootstrap ]; then
  step "Snapshotting the app folder"
  SNAPSHOT="$BACKUP_DIR/app-$TS.tgz"
  tar -czf "$SNAPSHOT" -C "$APP_ROOT" \
    --exclude='./tmp' --exclude='./MarketingIQ-main' --exclude='*.zip' --exclude='__pycache__' \
    --exclude='./.well-known' --exclude='./cgi-bin' --exclude='./stderr.log' .
  chmod 600 "$SNAPSHOT"
  rm -rf "$APP_GIT_DIR"
  git init -q --bare "$APP_GIT_DIR"
  appgit config core.bare false
  appgit config core.worktree "$APP_ROOT"
  appgit remote add origin "$REPO_URL"
fi

step "Fetching $DEPLOY_REF"
appgit fetch -q --depth 50 origin "$DEPLOY_REF"
TARGET=$(appgit rev-parse FETCH_HEAD)
if [ "$MODE" = update ] && [ "$PREVIOUS" = "$TARGET" ] && [ "${FORCE_DEPLOY:-false}" != "true" ]; then
  annotate notice "Deploy" "Already at ${TARGET:0:7}; nothing to deploy"
  exit 0
fi

restore_code() {
  if [ "$MODE" = bootstrap ]; then
    tar -xzf "$SNAPSHOT" -C "$APP_ROOT"
    rm -rf "$APP_GIT_DIR"
  else
    appgit reset -q --hard "$PREVIOUS"
  fi
  python -m pip install --quiet . || echo "::warning::Reinstalling the previous package failed"
}
rollback() {
  annotate error "Deploy failed" "Restored the previous code; the running site was not restarted"
  restore_code
}
trap rollback ERR

step "Updating code to ${TARGET:0:7} ($MODE)"
if [ "$MODE" = bootstrap ]; then
  appgit update-ref refs/heads/deploy "$TARGET"
  appgit symbolic-ref HEAD refs/heads/deploy
  appgit reset -q --mixed
  changed=$(appgit diff --name-only | wc -l | tr -d ' ')
  appgit checkout -q -- .
  echo "Bootstrap: $changed tracked file(s) differed from ${TARGET:0:7} and were updated"
else
  appgit reset -q --hard "$TARGET"
fi

step "Installing package"
python -m pip install --quiet .

step "Validating production configuration"
python -m marketingiq.jobs.validate_deployment >/dev/null

step "Backing up database"
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
DB_BACKUP="$BACKUP_DIR/db-$TS-${TARGET:0:7}.sql.gz"
mysqldump --single-transaction --no-tablespaces -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" \
  "$DB_NAME" | gzip > "$DB_BACKUP"
unset MYSQL_PWD
chmod 600 "$DB_BACKUP"
for pattern in 'db-*.sql.gz' 'app-*.tgz'; do
  ls -1t "$BACKUP_DIR"/$pattern 2>/dev/null | tail -n +"$((BACKUP_KEEP + 1))" | xargs -r rm -f
done

db_revision() {
  python - <<'PY'
from marketingiq.infrastructure.database import create_database_engine
from marketingiq.infrastructure.schema_status import inspect_database_schema

print(",".join(inspect_database_schema(create_database_engine()).current_heads))
PY
}
REVISION_BEFORE=$(db_revision)

step "Migrating database"
alembic upgrade head
REVISION_AFTER=$(db_revision)

step "Verifying schema"
python -m marketingiq.jobs.verify_database_schema >/dev/null

trap - ERR
step "Restarting Passenger"
mkdir -p tmp
touch tmp/restart.txt

healthy() {
  local url="${MARKETINGIQ_PUBLIC_URL:-}"
  [ -n "$url" ] || return 0
  for _ in 1 2 3 4 5 6; do
    [ "$(http_code "${url%/}/health/ready")" = "200" ] && return 0
    sleep 10
  done
  return 1
}

if ! healthy; then
  if [ "$REVISION_BEFORE" = "$REVISION_AFTER" ]; then
    restore_code
    touch tmp/restart.txt
    fail "The site was unhealthy after the restart; restored the previous code and restarted again"
  fi
  fail "The site is unhealthy after the restart and a migration ran ($REVISION_BEFORE -> $REVISION_AFTER); restore from $(basename "$DB_BACKUP") if needed"
fi

details=("Deployed ${TARGET:0:7} ($MODE)" "Database backup: $(basename "$DB_BACKUP")")
[ -n "$PREVIOUS" ] && details+=("Previous commit: ${PREVIOUS:0:7}")
[ -n "$SNAPSHOT" ] && details+=("App snapshot: $(basename "$SNAPSHOT")")
[ "$REVISION_BEFORE" = "$REVISION_AFTER" ] || details+=("Migrated: $REVISION_BEFORE -> $REVISION_AFTER")
annotate notice "Deploy" "${details[@]}"
