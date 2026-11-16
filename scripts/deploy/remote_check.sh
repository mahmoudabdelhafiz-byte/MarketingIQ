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
    python - <<'PY' 2>&1 | tail -n 4
from sqlalchemy import text

from marketingiq.infrastructure.database import create_database_engine
from marketingiq.infrastructure.schema_status import inspect_database_schema

engine = create_database_engine()
with engine.connect() as connection:
    print(f"Database: {connection.scalar(text('SELECT VERSION()'))}")
    # Arabic and other non-Latin text needs utf8mb4 end to end (connection, database, tables).
    conn_cs = connection.scalar(text("SELECT @@character_set_connection"))
    db_cs = connection.scalar(text("SELECT @@character_set_database"))
    rows = connection.execute(
        text(
            "SELECT COALESCE(c.character_set_name, '?') AS cs, COUNT(*) FROM information_schema.tables t "
            "LEFT JOIN information_schema.collation_character_set_applicability c "
            "ON c.collation_name = t.table_collation "
            "WHERE t.table_schema = DATABASE() GROUP BY cs"
        )
    ).all()
    tables = ", ".join(f"{cs}: {count}" for cs, count in rows)
    print(f"Character sets: connection {conn_cs}, database {db_cs}, tables {tables}")
status = inspect_database_schema(engine)
print(
    f"Schema: {status.state} (database {', '.join(status.current_heads) or 'none'}, "
    f"code expects {', '.join(status.expected_heads) or 'none'})"
)
PY
  )
  while IFS= read -r line; do [ -n "$line" ] && add "$line"; done <<< "$db_report"
fi

# Outbound email: build a message exactly as the app does, without connecting to SMTP.
if [ -n "$names" ] && [ -n "${APP_VENV:-}" ] && [ -f "$APP_VENV" ]; then
  smtp_report=$(
    load_cpanel_env
    # shellcheck disable=SC1090
    . "$APP_VENV"
    cd "$APP_ROOT" || exit 0
    LANG=C LC_ALL=C python - <<'PY' 2>&1 | tail -n 12
import os
import sys
import traceback

raw = {k: v for k, v in os.environ.items() if k.startswith(("SMTP_", "IMAP_", "MARKETINGIQ_"))}
non_ascii = sorted(k for k, v in raw.items() if any(ord(c) > 127 for c in v))
print(f"Locale: fs={sys.getfilesystemencoding()} preferred={__import__('locale').getpreferredencoding(False)}")
print(f"Email settings with non-ASCII values (names only): {', '.join(non_ascii) or 'none'}")
import marketingiq  # noqa: F401  (repairs surrogate-escaped settings)
from email import policy
from email.generator import BytesGenerator
from io import BytesIO

from marketingiq.domain.outbound import OutboundMessage
from marketingiq.infrastructure import outbound as ob

captured = {}


class Probe(ob.smtplib.SMTP):
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self, *a, **k):
        return (250, b"ok")

    def starttls(self, *a, **k):
        return (220, b"ok")

    def login(self, *a, **k):
        return (235, b"ok")

    def send_message(self, msg, *a, **k):
        buf = BytesIO()
        BytesGenerator(buf, policy=policy.SMTP).flatten(msg)
        captured["bytes"] = len(buf.getvalue())
        return {}


ob.smtplib.SMTP = Probe
ob.smtplib.SMTP_SSL = Probe
try:
    sender = ob.SMTPEmailSender()
    sender.send(
        OutboundMessage(
            recipient="probe@example.invalid",
            subject="E2E Test \u2014 \u0628\u0637\u0627\u0642\u0627\u062a",
            body="\u0645\u0631\u062d\u0628\u064b\u0627 Test\u060c",
            idempotency_key="probe",
            unsubscribe_url="https://example.invalid/u",
            language="ar",
        )
    )
    print(f"SMTP message build: OK ({captured.get('bytes')} bytes, nothing sent)")
except Exception:
    lines = traceback.format_exc().strip().splitlines()
    print("SMTP message build FAILED: " + " | ".join(line.strip() for line in lines[-8:]))
PY
  )
  while IFS= read -r line; do [ -n "$line" ] && add "$line"; done <<< "$smtp_report"
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

annotate notice "Server check" "${report[@]}"
if [ "${#warnings[@]}" -gt 0 ]; then
  annotate warning "Needs attention" "${warnings[@]}"
fi
printf '%s\n' "${report[@]}"
[ "${#warnings[@]}" -eq 0 ] || printf 'WARNING: %s\n' "${warnings[@]}"
exit 0
