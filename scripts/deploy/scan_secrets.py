"""Fail if a directory tree contains credential-looking content or files. Prints names only."""

from __future__ import annotations

import os
import re
import sys

PATTERNS = {
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "OpenAI/Anthropic-style key": re.compile(rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{24,}"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "Slack token": re.compile(rb"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Hunter-style 40-hex key assignment": re.compile(
        rb"(?i)hunter[a-z_]*\s*[:=]\s*['\"]?[0-9a-f]{40}\b"
    ),
    "credential in a URL": re.compile(
        rb"(?i)\b(?:mysql(?:\+pymysql)?|postgres(?:ql)?|smtp|imap)s?://[^\s:/@'\"]+:"
        rb"(?!change-me|password|PASSWORD|secret|replace-me|marketingiq_test|test|x@)"
        rb"[^\s@'\"/]{6,}@(?!127\.0\.0\.1|localhost|db\.example|host|HOST|example)"
    ),
}
FORBIDDEN_NAMES = re.compile(r"^(\.env(\..+)?|id_rsa.*|id_ed25519.*|.*\.(pem|key|p12|pfx|sql|sqlite3?|db))$")


def main(root: str) -> int:
    findings: list[str] = []
    for current, _dirs, files in os.walk(root):
        for name in files:
            rel = os.path.relpath(os.path.join(current, name), root)
            if FORBIDDEN_NAMES.match(name) and name != ".env.example":
                findings.append(f"{rel}: forbidden file type")
                continue
            with open(os.path.join(current, name), "rb") as handle:
                data = handle.read()
            for label, pattern in PATTERNS.items():
                if pattern.search(data):
                    findings.append(f"{rel}: {label}")
    if findings:
        message = "%0A".join(f.replace("%", "%25") for f in findings[:40])
        print(f"::error title=Secret scan failed::{message}")
        return 1
    print("::notice title=Secret scan::No credential patterns found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
