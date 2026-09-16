from __future__ import annotations

import json
import os

from marketingiq.application.automation_policies import ScheduledAutomationExecutor
from marketingiq.application.research import ProviderRegistry
from marketingiq.infrastructure.database import create_database_engine, create_session_factory
from marketingiq.infrastructure.providers import HunterProvider, PublicWebProvider


def main() -> None:
    raw_limit = os.environ.get("AUTOMATION_CRON_BATCH_SIZE", "20")
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise RuntimeError("AUTOMATION_CRON_BATCH_SIZE must be an integer") from error

    sessions = create_session_factory(create_database_engine())
    registry = ProviderRegistry([PublicWebProvider(), HunterProvider()])
    with sessions() as session:
        try:
            runs = ScheduledAutomationExecutor(session, registry).run_due(limit=limit)
            session.commit()
        except Exception:
            session.rollback()
            raise

    print(
        json.dumps(
            {
                "processed": len(runs),
                "completed": sum(run.status.value == "COMPLETED" for run in runs),
                "failed": sum(run.status.value == "FAILED" for run in runs),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
