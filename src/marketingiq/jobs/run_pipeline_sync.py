from __future__ import annotations

import json
import os

from marketingiq.application.pipeline_sync import ScheduledPipelineSyncService
from marketingiq.infrastructure.database import create_database_engine, create_session_factory


def main() -> None:
    raw_limit = os.environ.get("PIPELINE_SYNC_BATCH_SIZE", "100")
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise RuntimeError("PIPELINE_SYNC_BATCH_SIZE must be an integer") from error

    sessions = create_session_factory(create_database_engine())
    with sessions() as session:
        try:
            result = ScheduledPipelineSyncService(session).run(limit=limit)
            session.commit()
        except Exception:
            session.rollback()
            raise

    print(
        json.dumps(
            {
                "processed": result.processed,
                "created": result.created,
                "responded": result.responded,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
