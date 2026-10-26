"""Print shell ``export`` lines for the ``SetEnv`` values in the application's ``.htaccess``.

cPanel stores Python application environment variables as Apache ``SetEnv`` directives, which cron
jobs and interactive shells do not inherit. Load them before running a job::

    eval "$(python -m marketingiq.jobs.print_htaccess_env)"

Only uppercase variable names are exported, and values are shell-quoted.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

_SETENV = re.compile(r"^\s*SetEnv\s+([A-Z][A-Z0-9_]*)\s+(.*?)\s*$")


def export_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        match = _SETENV.match(raw)
        if not match:
            continue
        name, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        lines.append(f"export {name}={shlex.quote(value)}")
    return lines


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0] if args else os.environ.get("MARKETINGIQ_PROJECT_ROOT") or Path.cwd())
    path = root / ".htaccess"
    if not path.is_file():
        print(f"echo 'MarketingIQ: {shlex.quote(str(path))} not found' >&2")
        raise SystemExit(1)
    print("\n".join(export_lines(path.read_text(encoding="utf-8", errors="replace"))))


if __name__ == "__main__":
    main()
