# Runs one MarketingIQ scheduled job on the server now, with the app's cPanel settings.
# Expects common.sh to be prepended; JOB is one of the allowed job modules.
set -uo pipefail

case "${JOB:-}" in
  run_mailbox_engagement|run_follow_ups|run_pipeline_sync|run_automation_policies|run_company_discovery) ;;
  *) echo "::error::Unknown job '${JOB:-}'"; exit 1 ;;
esac

[ -n "${APP_VENV:-}" ] && [ -f "$APP_VENV" ] || { echo "::error::App virtualenv not found"; exit 1; }
load_cpanel_env || { echo "::error::Could not read the cPanel app settings"; exit 1; }
# shellcheck disable=SC1090
. "$APP_VENV"
cd "$APP_ROOT" || exit 1
echo "==> python -m marketingiq.jobs.${JOB}"
output=$(timeout 600 python -m "marketingiq.jobs.${JOB}" 2>&1)
status=$?
echo "$output" | tail -n 20
if [ $status -ne 0 ]; then
  echo "::error title=${JOB} failed::$(echo "$output" | tail -n 3 | tr '\n' ' ')"
  exit $status
fi
echo "::notice title=${JOB}::$(echo "$output" | tail -n 1)"
