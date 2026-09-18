from __future__ import annotations

import json

from marketingiq.infrastructure.database import create_database_engine
from marketingiq.infrastructure.schema_status import inspect_database_schema


def main() -> None:
    status = inspect_database_schema(create_database_engine())
    print(json.dumps(status.as_dict(), sort_keys=True))
    if not status.ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
