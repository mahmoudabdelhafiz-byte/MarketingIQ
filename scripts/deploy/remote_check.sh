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

# AI providers: free, read-only key and model-access probes (no searches or completions).
if [ -n "$names" ]; then
  ai_report=$(
    load_cpanel_env
    python3 - <<'PY' 2>&1 | tail -n 8
import json
import os
import urllib.error
import urllib.request

DEFAULTS = {"CAMPAIGN": "gpt-5.6-luna", "DISCOVERY": "gpt-5.5", "DECISION_MAKER": "gpt-5.6-luna"}


def get(url, headers):
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read() or b"{}")
        except ValueError:
            body = {}
        return error.code, body
    except Exception as error:  # noqa: BLE001
        return 0, {"error": {"code": type(error).__name__}}


def code_of(body):
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("code") or err.get("type") or "")
    return ""


provider = (os.environ.get("AI_PROVIDER") or "openai").lower()
key = os.environ.get("OPENAI_API_KEY", "").strip()
if key:
    headers = {"Authorization": "Bearer " + key}
    status, body = get("https://api.openai.com/v1/models", headers)
    print(f"OpenAI key: GET /v1/models -> {status} {code_of(body)}".rstrip())
    if status == 200:
        available = {m.get("id") for m in body.get("data", []) if isinstance(m, dict)}
        for feature, default in DEFAULTS.items():
            env = {"CAMPAIGN": "CAMPAIGN_AI_MODEL", "DISCOVERY": "DISCOVERY_AI_MODEL",
                   "DECISION_MAKER": "ROLE_RESOLVER_MODEL"}[feature]
            model = os.environ.get(env, "").strip() or default
            status, body = get("https://api.openai.com/v1/models/" + model, headers)
            listed = "listed" if model in available else "not listed"
            print(f"OpenAI {feature.lower()} model {model}: {status} {code_of(body)} ({listed})".rstrip())
        gpt = sorted(m for m in available if m and m.startswith("gpt-5"))[-6:]
        print("OpenAI gpt-5 models on this key: " + (", ".join(gpt) or "none"))
for name, url, hdr in (
    ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/models", "x-api-key"),
    ("DEEPSEEK_API_KEY", "https://api.deepseek.com/models", "Authorization"),
):
    key = os.environ.get(name, "").strip()
    if key:
        value = key if hdr == "x-api-key" else "Bearer " + key
        headers = {hdr: value, "anthropic-version": "2023-06-01"}
        status, body = get(url, headers)
        print(f"{name.split('_')[0].title()} key: GET models -> {status} {code_of(body)}".rstrip())
print(f"AI_PROVIDER: {provider}")
PY
  )
  while IFS= read -r line; do [ -n "$line" ] && add "$line"; done <<< "$ai_report"
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

# TEMPORARY diagnostics (never merged): why contacts are blocked. Counts and kinds only.
diag=$(
  load_cpanel_env
  . "$APP_VENV"
  cd "$APP_ROOT"
  python - <<'PY' 2>&1 | tail -n 25
from collections import Counter
from sqlalchemy import select, func
from marketingiq.infrastructure.database import create_database_engine
from sqlalchemy.orm import Session
import marketingiq.domain.campaigns  # noqa
from marketingiq.domain.models import ICP, ICPCriterion, LeadQualification, ProductFitAssessment, CompanyFact, OrganizationCompany, Product

with Session(create_database_engine()) as s:
    for p in s.scalars(select(Product)):
        icp = s.scalar(select(ICP).where(ICP.product_id == p.id, ICP.is_active.is_(True)).order_by(ICP.version.desc()))
        crit = [] if icp is None else [f"{c.kind}{'*' if c.required else ''}(w{c.weight})" for c in icp.criteria]
        print(f"product#{p.id[:4]} status={p.status.value} buyer_roles={len(p.primary_buyer_roles or [])} icp={'v'+str(icp.version) if icp else 'none'} emp={icp.employee_min if icp else None}-{icp.employee_max if icp else None} criteria={' '.join(crit) or 'none'}")
    print("companies:", s.scalar(select(func.count(OrganizationCompany.id))))
    latest = {}
    for q in s.scalars(select(LeadQualification).order_by(LeadQualification.qualified_at)):
        latest[(q.organization_company_id, q.product_id)] = q
    print("latest qualification status:", dict(Counter(q.status.value for q in latest.values())))
    gaps = Counter()
    for q in latest.values():
        for g in (q.research_gaps or []):
            gaps[(g.get("code"), g.get("fact_key"))] += 1
    print("research gaps:", dict(gaps.most_common(8)))
    results = Counter()
    for a in s.scalars(select(ProductFitAssessment)):
        for c in (a.explanation or {}).get("criteria", []):
            results[(c.get("type"), c.get("result"))] += 1
    print("fit criterion results:", dict(results.most_common(12)))
    keys = Counter(r[0] for r in s.execute(select(CompanyFact.fact_key)))
    print("fact keys stored:", dict(keys.most_common(12)))
    cov = [a.evidence_coverage for a in s.scalars(select(ProductFitAssessment))]
    print("coverage: n=%d avg=%s" % (len(cov), round(sum(cov)/len(cov)) if cov else None))
PY
)
annotate notice "Diagnostics" "$diag"

annotate notice "Server check" "${report[@]}"
if [ "${#warnings[@]}" -gt 0 ]; then
  annotate warning "Needs attention" "${warnings[@]}"
fi
printf '%s\n' "${report[@]}"
[ "${#warnings[@]}" -eq 0 ] || printf 'WARNING: %s\n' "${warnings[@]}"
exit 0
