from __future__ import annotations

import json
import os

from marketingiq.infrastructure.database import create_database_engine, create_session_factory
from marketingiq.infrastructure.job_lock import mysql_job_lock
from marketingiq.infrastructure.mailbox_checkpoint import CheckpointedIMAPMailboxReader
from marketingiq.infrastructure.mailbox_engagement import MailboxEngagementIngestor


def main() -> None:
    raw_limit = os.environ.get("MAILBOX_ENGAGEMENT_BATCH_SIZE", "100")
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise RuntimeError("MAILBOX_ENGAGEMENT_BATCH_SIZE must be an integer") from error

    engine = create_database_engine()
    with mysql_job_lock(engine, "marketingiq:mailbox-engagement") as acquired:
        if not acquired:
            print(json.dumps({"reason": "already_running", "status": "skipped"}, sort_keys=True))
            return
        sessions = create_session_factory(engine)
        with sessions() as session:
            reader = CheckpointedIMAPMailboxReader(session)
            try:
                result = MailboxEngagementIngestor(session, reader).run(limit=limit)
                session.commit()
            except Exception:
                session.rollback()
                raise

    print(
        json.dumps(
            {
                "processed": result.processed,
                "matched": result.matched,
                "replies": result.replies,
                "bounces": result.bounces,
                "skipped": result.skipped,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
