import os

import pytest
from sqlalchemy import create_engine

from marketingiq.infrastructure.job_lock import mysql_job_lock

pytestmark = pytest.mark.mysql


def test_mysql_job_lock_allows_only_one_holder_and_releases_after_exit():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    engine = create_engine(url)
    lock_name = "test-single-run"

    try:
        with mysql_job_lock(engine, lock_name) as first:
            assert first is True
            with mysql_job_lock(engine, lock_name) as second:
                assert second is False

        with mysql_job_lock(engine, lock_name) as third:
            assert third is True
    finally:
        engine.dispose()
