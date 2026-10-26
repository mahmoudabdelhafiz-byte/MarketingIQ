"""Cron entry point: run due company discovery rules across tenants.

    python -m marketingiq.jobs.run_company_discovery
"""

from __future__ import annotations

import json
import os

from marketingiq.application.discovery import CompanyDiscoveryExecutor
from marketingiq.application.research import ProviderRegistry
from marketingiq.infrastructure.ai_discovery import KEY as AI_SOURCE_KEY
from marketingiq.infrastructure.ai_discovery import build_ai_discovery_provider
from marketingiq.infrastructure.database import create_database_engine, create_session_factory
from marketingiq.infrastructure.discovery import HunterDiscoveryProvider
from marketingiq.infrastructure.job_lock import mysql_job_lock
from marketingiq.infrastructure.providers import HunterProvider, PublicWebProvider


def main() -> None:
    raw_limit = os.environ.get("DISCOVERY_CRON_BATCH_SIZE", "5")
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise RuntimeError("DISCOVERY_CRON_BATCH_SIZE must be an integer") from error

    engine = create_database_engine()
    registry = ProviderRegistry([PublicWebProvider(), HunterProvider()])
    with mysql_job_lock(engine, "company-discovery") as acquired:
        if not acquired:
            print(json.dumps({"reason": "already_running", "status": "skipped"}, sort_keys=True))
            return
        sessions = create_session_factory(engine)
        with sessions() as session:
            try:
                runs = CompanyDiscoveryExecutor(
                    session,
                    HunterDiscoveryProvider(),
                    registry,
                    extra_providers={AI_SOURCE_KEY: build_ai_discovery_provider()},
                ).run_due(limit=limit)
                session.commit()
            except Exception:
                session.rollback()
                raise

    print(
        json.dumps(
            {
                "processed": len(runs),
                "companies_added": sum(run.companies_added for run in runs),
                "failed": sum(run.status.value == "FAILED" for run in runs),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
