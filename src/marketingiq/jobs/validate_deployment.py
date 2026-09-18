from __future__ import annotations

import json

from marketingiq.infrastructure.deployment_config import validate_deployment_environment


def main() -> None:
    report = validate_deployment_environment()
    print(json.dumps(report.as_dict(), sort_keys=True))
    if not report.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
