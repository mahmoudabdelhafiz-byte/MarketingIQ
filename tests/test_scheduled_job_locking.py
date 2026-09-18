from contextlib import contextmanager

import pytest

from marketingiq.jobs import (
    run_automation_policies,
    run_mailbox_engagement,
    run_pipeline_sync,
)


@contextmanager
def _unavailable_lock(_engine, _name):
    yield False


@pytest.mark.parametrize(
    "job_module",
    [
        run_automation_policies,
        run_mailbox_engagement,
        run_pipeline_sync,
    ],
)
def test_scheduled_job_skips_cleanly_when_same_job_is_already_running(
    job_module,
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(job_module, "create_database_engine", lambda: object())
    monkeypatch.setattr(job_module, "mysql_job_lock", _unavailable_lock)

    job_module.main()

    assert capsys.readouterr().out.strip() == (
        '{"reason": "already_running", "status": "skipped"}'
    )
