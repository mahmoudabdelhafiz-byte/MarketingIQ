from __future__ import annotations

import json
import os

from marketingiq.application.pipeline_sync import ScheduledPipelineSyncService
from marketingiq.infrastructure.database import create_database_engine, create_session_factory
from marketingiq.infrastructure.job_lock import mysql_job_lock


def main() -> None:
    raw_limit = os.environ.get("PIPELINE_SYNC_BATCH_SIZE", "100")
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise RuntimeError("PIPELINE_SYNC_BATCH_SIZE must be an integer") from error

    engine = create_database_engine()
    with mysql_job_lock(engine, "pipeline-sync") as acquired:
        if not acquired:
            print(json.dumps({"reason": "already_running", "status": "skipped"}, sort_keys=True))
            return
        sessions = create_session_factory(engine)
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
