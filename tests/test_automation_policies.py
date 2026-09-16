from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_campaigns import SECRET, login, seed_api_database, seed_campaign

from marketingiq.api.app import create_app
from marketingiq.application.authorization import AuthorizationError
from marketingiq.application.automation_policies import (
    AutomationPolicyService,
    ScheduledAutomationExecutor,
)
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.automation import (
    AutomationPolicy,
    AutomationPolicyRun,
    AutomationPolicyRunStatus,
)
from marketingiq.domain.models import (
    AuditLog,
    ContactCandidate,
    MembershipRole,
    OrganizationMembership,
    Product,
)

NOW = datetime(2026, 9, 17, 1, 0, tzinfo=UTC)


def _admin_service(session: Session, seeded):
    return AutomationPolicyService(
        session,
        TenantContext(
            seeded["organization"].id,
            seeded["user"].id,
            MembershipRole.ORGANIZATION_ADMIN,
        ),
        ProviderRegistry([]),
        now=NOW,
    )


def _add_membership(session: Session, seeded, role=MembershipRole.ORGANIZATION_ADMIN):
    membership = OrganizationMembership(
        organization_id=seeded["organization"].id,
        user_id=seeded["user"].id,
        role=role,
    )
    session.add(membership)
    session.flush()
    return membership


def test_due_policy_stops_at_human_gate_and_advances_schedule(session):
    seeded = seed_campaign(session)
    _add_membership(session, seeded)
    policy = _admin_service(session, seeded).create(
        seeded["relationship"].id,
        seeded["product"].id,
        cadence_minutes=60,
        next_run_at=NOW,
    )

    runs = ScheduledAutomationExecutor(session, ProviderRegistry([]), now=NOW).run_due()

    assert len(runs) == 1
    run = runs[0]
    assert run.policy_id == policy.id
    assert run.status == AutomationPolicyRunStatus.COMPLETED
    assert run.stop_reason == "HUMAN_CAMPAIGN_GATE"
    assert run.executed_steps == []
    assert policy.last_status == "COMPLETED"
    assert policy.last_error_code is None
    assert policy.next_run_at == NOW + timedelta(hours=1)

    assert ScheduledAutomationExecutor(session, ProviderRegistry([]), now=NOW).run_due() == []
    stored_runs = list(session.scalars(select(AutomationPolicyRun)))
    assert len(stored_runs) == 1

    audits = list(
        session.scalars(
            select(AuditLog).where(AuditLog.action == "automation.policy.run.completed")
        )
    )
    assert len(audits) == 1
    assert "email" not in str(audits[0].metadata_json).lower()


def test_policy_without_credit_approval_stops_before_contact_provider(session):
    seeded = seed_campaign(session)
    _add_membership(session, seeded)
    session.delete(seeded["email"])
    session.delete(seeded["contact"])
    session.flush()
    policy = _admin_service(session, seeded).create(
        seeded["relationship"].id,
        seeded["product"].id,
        cadence_minutes=60,
        allow_provider_credits=False,
        next_run_at=NOW,
    )

    run = ScheduledAutomationExecutor(session, ProviderRegistry([]), now=NOW).run_due()[0]

    assert run.status == AutomationPolicyRunStatus.COMPLETED
    assert run.stop_reason == "PROVIDER_CREDIT_APPROVAL_REQUIRED"
    assert run.executed_steps == []
    assert policy.allow_provider_credits is False
    assert session.scalar(select(ContactCandidate)) is None


def test_scheduled_execution_rechecks_run_as_membership(session):
    seeded = seed_campaign(session)
    membership = _add_membership(session, seeded)
    policy = _admin_service(session, seeded).create(
        seeded["relationship"].id,
        seeded["product"].id,
        cadence_minutes=60,
        next_run_at=NOW,
    )
    membership.role = MembershipRole.READ_ONLY
    session.flush()

    run = ScheduledAutomationExecutor(session, ProviderRegistry([]), now=NOW).run_due()[0]

    assert run.status == AutomationPolicyRunStatus.FAILED
    assert run.error_code == "AUTOMATION_RUN_AS_PERMISSION_DENIED"
    assert policy.last_status == "FAILED"
    assert policy.last_error_code == "AUTOMATION_RUN_AS_PERMISSION_DENIED"


def test_only_admin_can_manage_policy_but_reader_can_view(session):
    seeded = seed_campaign(session)
    admin = _admin_service(session, seeded)
    policy = admin.create(
        seeded["relationship"].id,
        seeded["product"].id,
        cadence_minutes=60,
        enabled=False,
    )
    reader = AutomationPolicyService(
        session,
        TenantContext(
            seeded["organization"].id,
            seeded["user"].id,
            MembershipRole.READ_ONLY,
        ),
        ProviderRegistry([]),
        now=NOW,
    )

    assert reader.get(policy.id).id == policy.id
    assert [item.id for item in reader.list()] == [policy.id]
    with pytest.raises(AuthorizationError):
        reader.create(
            seeded["relationship"].id,
            seeded["product"].id,
            cadence_minutes=60,
        )
    with pytest.raises(AuthorizationError):
        reader.run_due()


def test_automation_policy_api_admin_management_and_read_only_rbac(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'automation-policy-api.db'}"
    seeded = seed_api_database(url)
    engine = create_engine(url)
    with Session(engine) as session:
        membership = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == seeded["org_id"],
                OrganizationMembership.role == MembershipRole.MARKETING_USER,
            )
        )
        assert membership is not None
        membership.role = MembershipRole.ORGANIZATION_ADMIN
        admin_user_id = membership.user_id
        product_id = session.scalar(select(Product.id))
        session.commit()
    assert product_id is not None

    client = TestClient(create_app(url, SECRET))
    admin = login(client, seeded["marketing_email"])
    reader = login(client, seeded["reader_email"])
    base = f"/api/v1/organizations/{seeded['org_id']}/automation-policies"
    payload = {
        "relationship_id": seeded["relationship_id"],
        "product_id": product_id,
        "cadence_minutes": 60,
        "enabled": False,
    }

    denied = client.post(base, headers=reader, json=payload)
    assert denied.status_code == 403

    created = client.post(base, headers=admin, json=payload)
    assert created.status_code == 201
    policy_id = created.json()["id"]
    assert created.json()["run_as_user_id"] == admin_user_id

    listing = client.get(base, headers=reader)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [policy_id]

    denied_run = client.post(base + "/run-due", headers=reader, json={})
    assert denied_run.status_code == 403
    allowed_run = client.post(base + "/run-due", headers=admin, json={})
    assert allowed_run.status_code == 200
    assert allowed_run.json() == []

    with Session(engine) as session:
        assert session.scalar(select(AutomationPolicy).where(AutomationPolicy.id == policy_id))
