"""Cron job: move due follow-ups forward (queue for approval, or send automatic ones)."""

from __future__ import annotations

import json
import os

from marketingiq.application.follow_ups import FollowUpExecutor
from marketingiq.application.outbound import OutboundSenderRegistry
from marketingiq.application.unsubscribe import UnsubscribeLinks
from marketingiq.infrastructure.database import create_database_engine, create_session_factory
from marketingiq.infrastructure.job_lock import mysql_job_lock
from marketingiq.infrastructure.outbound import SMTPEmailSender


def main() -> None:
    raw_limit = os.environ.get("FOLLOW_UP_BATCH_SIZE", "50")
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise RuntimeError("FOLLOW_UP_BATCH_SIZE must be an integer") from error

    base_url = os.environ.get("MARKETINGIQ_PUBLIC_URL", "").strip()
    secret = os.environ.get("AUTH_SECRET", "")
    # The same signed one-click unsubscribe link as first emails; refuse to send without it.
    links = UnsubscribeLinks(secret, base_url) if base_url and secret else None

    engine = create_database_engine()
    with mysql_job_lock(engine, "follow-ups") as acquired:
        if not acquired:
            print(json.dumps({"reason": "already_running", "status": "skipped"}, sort_keys=True))
            return
        sessions = create_session_factory(engine)
        with sessions() as session:
            try:
                result = FollowUpExecutor(
                    session,
                    OutboundSenderRegistry([SMTPEmailSender()]),
                    unsubscribe_links=links,
                ).run_due(limit=limit)
                session.commit()
            except Exception:
                session.rollback()
                raise
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
