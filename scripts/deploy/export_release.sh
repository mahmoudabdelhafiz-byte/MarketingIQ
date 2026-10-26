#!/usr/bin/env bash
# Package the live MarketingIQ source for import into GitHub. Read-only for the application:
# it only writes ~/.marketingiq/export/release.tgz. Before packaging, every file is checked for
# the actual values of sensitive cPanel app settings; any hit aborts with file and setting
# names only (values are never printed).
set -euo pipefail

if [ -z "${APP_ROOT:-}" ]; then
  venv_dir=$(ls -d "$HOME"/virtualenv/*/3.12* 2>/dev/null | head -n 1)
  APP_ROOT="$HOME/$(basename "$(dirname "${venv_dir:?no cPanel 3.12 virtualenv found}")")"
fi
EXPORT_DIR="$HOME/.marketingiq/export"
mkdir -p "$EXPORT_DIR"
chmod 700 "$HOME/.marketingiq" "$EXPORT_DIR"
rm -f "$EXPORT_DIR/release.tgz"

python3 - "$APP_ROOT" "${APP_ROOT#"$HOME"/}" "$EXPORT_DIR/release.tgz" <<'PY'
import fnmatch
import json
import os
import re
import subprocess
import sys
import tarfile
from urllib.parse import unquote, urlsplit

app_root, rel_root, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

EXCLUDED_DIRS = {
    ".git", ".github", "tmp", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "build", "dist", "node_modules", "venv", ".venv", "logs", ".well-known", "cgi-bin",
    "MarketingIQ-main",
}
EXCLUDED_DIR_PATTERNS = ["*.egg-info"]
EXCLUDED_FILES = [
    ".htaccess*", "*.zip", "*.tar", "*.tar.gz", "*.tgz", "*.pyc", "*.pyo", "*.log",
    "passenger_wsgi.py", "stderr*", ".DS_Store", "*.sql", "*.sql.gz", "*.sqlite*", "*.db",
    "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "*.bak", "*.swp", "*~",
]
MAX_BYTES = 2_000_000
STRONG_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "OpenAI/Anthropic-style key": re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{24,}"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "Slack token": re.compile(rb"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b"),
}
SENSITIVE_NAME = re.compile(r"(KEY|SECRET|PASSWORD|PASSWD|TOKEN|PASS)$|^DATABASE_URL$")


def annotate(level, title, lines):
    message = "\n".join(lines).replace("%", "%25").replace("\n", "%0A")
    print(f"::{level} title={title}::{message}")


def live_settings():
    raw = subprocess.run(
        ["cloudlinux-selector", "get", "--json", "--interpreter", "python"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, check=False,
    ).stdout
    for version in (json.loads(raw or "{}").get("available_versions") or {}).values():
        for user in ((version or {}).get("users") or {}).values():
            app = ((user or {}).get("applications") or {}).get(rel_root)
            if app is not None:
                return app.get("env_vars") or {}
    return None


settings = live_settings()
if settings is None:
    annotate("error", "Export aborted", [f"Could not read cPanel settings for {rel_root}"])
    sys.exit(1)

needles = {}
for name, value in settings.items():
    value = str(value or "")
    if SENSITIVE_NAME.search(name) and len(value) >= 6:
        needles[name] = value.encode()
    if name == "DATABASE_URL":
        password = unquote(urlsplit(value).password or "")
        if len(password) >= 6:
            needles["DATABASE_URL password"] = password.encode()

included, excluded, oversized, findings = [], [], [], []
for current, dirs, files in os.walk(app_root):
    rel_dir = os.path.relpath(current, app_root)
    keep = []
    for name in sorted(dirs):
        if name in EXCLUDED_DIRS or any(fnmatch.fnmatch(name, p) for p in EXCLUDED_DIR_PATTERNS):
            excluded.append(os.path.normpath(os.path.join(rel_dir, name)) + "/")
        elif os.path.islink(os.path.join(current, name)):
            excluded.append(os.path.normpath(os.path.join(rel_dir, name)) + "/ (symlink)")
        else:
            keep.append(name)
    dirs[:] = keep
    for name in sorted(files):
        rel = os.path.normpath(os.path.join(rel_dir, name))
        path = os.path.join(current, name)
        is_env = name.startswith(".env") and name != ".env.example"
        if is_env or any(fnmatch.fnmatch(name, p) for p in EXCLUDED_FILES) or os.path.islink(path):
            excluded.append(rel)
            continue
        if os.path.getsize(path) > MAX_BYTES:
            oversized.append(f"{rel} ({os.path.getsize(path) // 1024} KB)")
            continue
        with open(path, "rb") as handle:
            data = handle.read()
        for label, needle in needles.items():
            if needle in data:
                findings.append(f"{rel}: contains the live value of {label}")
        for label, pattern in STRONG_PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{rel}: looks like a {label}")
        included.append(rel)

if findings:
    annotate("error", "Export aborted: secrets found", findings[:40])
    sys.exit(1)

with tarfile.open(out_path, "w:gz") as archive:
    for rel in included:
        archive.add(os.path.join(app_root, rel), arcname=rel, recursive=False)
os.chmod(out_path, 0o600)

annotate("notice", "Export", [
    f"Packaged {len(included)} files from {rel_root}; checked against {len(needles)} live secret values",
    "Excluded: " + " ".join(excluded[:40]) + (" ..." if len(excluded) > 40 else ""),
    "Too large, skipped: " + (" ".join(oversized) or "none"),
])
PY
