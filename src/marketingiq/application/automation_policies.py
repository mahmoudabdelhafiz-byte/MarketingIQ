from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from marketingiq.application.authorization import (
    ROLE_PERMISSIONS,
    Permission,
    require_permission,
)
from marketingiq.application.errors import AuthorizationError, ConflictError, NotFoundError
from marketingiq.application.orchestration import AutomationOrchestrationService
from marketingiq.application.research import ProviderRegistry
from marketingiq.application.tenant import TenantContext
from marketingiq.domain.automation import (
    AutomationPolicy,
    AutomationPolicyRun,
    AutomationPolicyRunStatus,
)
from marketingiq.domain.models import (
    AuditLog,
    OrganizationCompany,
    OrganizationMembership,
    Product,
    User,
)

MIN_CADENCE_MINUTES = 60
MAX_CADENCE_MINUTES = 43200
MAX_DUE_BATCH = 100


class AutomationPolicyService:
    def __init__(
        self,
        session: Session,
        tenant: TenantContext,
        provider_registry: ProviderRegistry,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.provider_registry = provider_registry
        self.now = _as_utc(now or datetime.now(UTC))

    def list(self) -> list[AutomationPolicy]:
        require_permission(self.tenant, Permission.READ)
        return list(
            self.session.scalars(
                select(AutomationPolicy)
                .where(AutomationPolicy.organization_id == self.tenant.organization_id)
                .order_by(AutomationPolicy.created_at.desc(), AutomationPolicy.id.desc())
            )
        )

    def get(self, policy_id: str) -> AutomationPolicy:
        require_permission(self.tenant, Permission.READ)
        policy = self.session.scalar(
            select(AutomationPolicy).where(
                AutomationPolicy.id == policy_id,
                AutomationPolicy.organization_id == self.tenant.organization_id,
            )
        )
        if policy is None:
            raise NotFoundError("Automation policy not found")
        return policy

    def create(
        self,
        relationship_id: str,
        product_id: str,
        *,
        cadence_minutes: int,
        enabled: bool = True,
        allow_provider_credits: bool = False,
        contact_provider: str = "HUNTER",
        max_contacts: int = 10,
        max_steps: int = 4,
        next_run_at: datetime | None = None,
    ) -> AutomationPolicy:
        require_permission(self.tenant, Permission.MANAGE_AUTOMATION_POLICIES)
        self._relationship(relationship_id)
        self._product(product_id)
        self._validate_options(
            cadence_minutes,
            allow_provider_credits,
            contact_provider,
            max_contacts,
            max_steps,
        )
        existing = self.session.scalar(
            select(AutomationPolicy.id).where(
                AutomationPolicy.organization_id == self.tenant.organization_id,
                AutomationPolicy.organization_company_id == relationship_id,
                AutomationPolicy.product_id == product_id,
            )
        )
        if existing is not None:
            raise ConflictError("AUTOMATION_POLICY_ALREADY_EXISTS")

        actor = self.tenant.actor_user_id
        policy = AutomationPolicy(
            organization_id=self.tenant.organization_id,
            organization_company_id=relationship_id,
            product_id=product_id,
            enabled=enabled,
            cadence_minutes=cadence_minutes,
            allow_provider_credits=allow_provider_credits,
            contact_provider=contact_provider.upper(),
            max_contacts=max_contacts,
            max_steps=max_steps,
            next_run_at=_as_utc(next_run_at or self.now),
            run_as_user_id=actor,
            created_by_user_id=actor,
        )
        self.session.add(policy)
        self.session.flush()
        self._audit("automation.policy.created", policy)
        return policy

    def update(self, policy_id: str, changes: dict) -> AutomationPolicy:
        require_permission(self.tenant, Permission.MANAGE_AUTOMATION_POLICIES)
        policy = self.get(policy_id)
        allowed = {
            "enabled",
            "cadence_minutes",
            "allow_provider_credits",
            "contact_provider",
            "max_contacts",
            "max_steps",
            "next_run_at",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported automation policy fields: {', '.join(sorted(unknown))}")
        if not changes:
            return policy

        cadence = changes.get("cadence_minutes", policy.cadence_minutes)
        allow_credits = changes.get("allow_provider_credits", policy.allow_provider_credits)
        provider = changes.get("contact_provider", policy.contact_provider)
        max_contacts = changes.get("max_contacts", policy.max_contacts)
        max_steps = changes.get("max_steps", policy.max_steps)
        self._validate_options(cadence, allow_credits, provider, max_contacts, max_steps)

        for key, value in changes.items():
            if value is None:
                raise ValueError(f"{key} cannot be null")
            if key == "contact_provider":
                value = value.upper()
            if key == "next_run_at":
                value = _as_utc(value)
            setattr(policy, key, value)
        policy.updated_at = self.now
        self.session.flush()
        self._audit("automation.policy.updated", policy)
        return policy

    def runs(self, policy_id: str) -> list[AutomationPolicyRun]:
        self.get(policy_id)
        return list(
            self.session.scalars(
                select(AutomationPolicyRun)
                .where(
                    AutomationPolicyRun.organization_id == self.tenant.organization_id,
                    AutomationPolicyRun.policy_id == policy_id,
                )
                .order_by(
                    AutomationPolicyRun.scheduled_for.desc(),
                    AutomationPolicyRun.id.desc(),
                )
            )
        )

    def run_due(self, *, limit: int = 20) -> list[AutomationPolicyRun]:
        require_permission(self.tenant, Permission.MANAGE_AUTOMATION_POLICIES)
        executor = ScheduledAutomationExecutor(
            self.session,
            self.provider_registry,
            now=self.now,
        )
        return executor.run_due(
            organization_id=self.tenant.organization_id,
            limit=limit,
            triggered_by_user_id=self.tenant.actor_user_id,
        )

    def _relationship(self, relationship_id: str) -> OrganizationCompany:
        relationship = self.session.scalar(
            select(OrganizationCompany).where(
                OrganizationCompany.id == relationship_id,
                OrganizationCompany.organization_id == self.tenant.organization_id,
            )
        )
        if relationship is None:
            raise NotFoundError("Company relationship not found")
        return relationship

    def _product(self, product_id: str) -> Product:
        product = self.session.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.organization_id == self.tenant.organization_id,
            )
        )
        if product is None:
            raise NotFoundError("Product not found")
        return product

    def _validate_options(
        self,
        cadence_minutes: int,
        allow_provider_credits: bool,
        contact_provider: str,
        max_contacts: int,
        max_steps: int,
    ) -> None:
        if not MIN_CADENCE_MINUTES <= cadence_minutes <= MAX_CADENCE_MINUTES:
            raise ValueError("cadence_minutes must be between 60 and 43200")
        if not 1 <= max_contacts <= 50:
            raise ValueError("max_contacts must be between 1 and 50")
        if not 1 <= max_steps <= 4:
            raise ValueError("max_steps must be between 1 and 4")
        if not contact_provider or len(contact_provider) > 100:
            raise ValueError("contact_provider must be between 1 and 100 characters")
        if allow_provider_credits:
            self.provider_registry.get(contact_provider)

    def _audit(self, action: str, policy: AutomationPolicy) -> None:
        self.session.add(
            AuditLog(
                organization_id=self.tenant.organization_id,
                actor_user_id=self.tenant.actor_user_id,
                action=action,
                entity_type="automation_policy",
                entity_id=policy.id,
                metadata_json={
                    "relationship_id": policy.organization_company_id,
                    "product_id": policy.product_id,
                    "enabled": policy.enabled,
                    "allow_provider_credits": policy.allow_provider_credits,
                    "cadence_minutes": policy.cadence_minutes,
                },
            )
        )
        self.session.flush()


class ScheduledAutomationExecutor:
    """Cron-safe executor for due pre-outreach policies.

    The unique policy/scheduled-for run record is the idempotency boundary. Every
    execution revalidates the stored run-as user and their current tenant role.
    """

    def __init__(
        self,
        session: Session,
        provider_registry: ProviderRegistry,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.provider_registry = provider_registry
        self.now = _as_utc(now or datetime.now(UTC))

    def run_due(
        self,
        *,
        organization_id: str | None = None,
        limit: int = 20,
        triggered_by_user_id: str | None = None,
    ) -> list[AutomationPolicyRun]:
        if not 1 <= limit <= MAX_DUE_BATCH:
            raise ValueError("limit must be between 1 and 100")
        query = select(AutomationPolicy).where(
            AutomationPolicy.enabled.is_(True),
            AutomationPolicy.next_run_at <= self.now,
        )
        if organization_id is not None:
            query = query.where(AutomationPolicy.organization_id == organization_id)
        policies = list(
            self.session.scalars(
                query.order_by(AutomationPolicy.next_run_at, AutomationPolicy.id).limit(limit)
            )
        )
        runs: list[AutomationPolicyRun] = []
        for policy in policies:
            run = self._execute_policy(policy, triggered_by_user_id=triggered_by_user_id)
            if run is not None:
                runs.append(run)
        return runs

    def _execute_policy(
        self,
        policy: AutomationPolicy,
        *,
        triggered_by_user_id: str | None,
    ) -> AutomationPolicyRun | None:
        scheduled_for = _as_utc(policy.next_run_at)
        existing = self.session.scalar(
            select(AutomationPolicyRun).where(
                AutomationPolicyRun.policy_id == policy.id,
                AutomationPolicyRun.scheduled_for == scheduled_for,
            )
        )
        if existing is not None:
            policy.next_run_at = _next_run_after(
                scheduled_for,
                policy.cadence_minutes,
                self.now,
            )
            self.session.flush()
            return None

        run = AutomationPolicyRun(
            organization_id=policy.organization_id,
            policy_id=policy.id,
            scheduled_for=scheduled_for,
            started_at=self.now,
            status=AutomationPolicyRunStatus.RUNNING,
            executed_steps=[],
            run_as_user_id=policy.run_as_user_id,
            triggered_by_user_id=triggered_by_user_id,
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
        except IntegrityError:
            return None

        policy.last_run_at = self.now
        policy.next_run_at = _next_run_after(
            scheduled_for,
            policy.cadence_minutes,
            self.now,
        )

        tenant = self._execution_tenant(policy)
        if tenant is None:
            self._finish_failure(policy, run, "AUTOMATION_RUN_AS_PERMISSION_DENIED")
            self._audit_run(policy, run)
            self.session.flush()
            return run

        try:
            with self.session.begin_nested():
                result = AutomationOrchestrationService(
                    self.session,
                    tenant,
                    self.provider_registry,
                ).run_until_gate(
                    policy.organization_company_id,
                    policy.product_id,
                    allow_provider_credits=policy.allow_provider_credits,
                    contact_provider=policy.contact_provider,
                    max_contacts=policy.max_contacts,
                    max_steps=policy.max_steps,
                )
            run.status = AutomationPolicyRunStatus.COMPLETED
            run.stop_reason = result["stop_reason"]
            run.executed_steps = [item["step"] for item in result["executed_steps"]]
            run.completed_at = self.now
            policy.last_status = run.status.value
            policy.last_error_code = None
        except AuthorizationError:
            self._finish_failure(policy, run, "AUTOMATION_PERMISSION_DENIED")
        except ConflictError as error:
            code = str(error)
            if not code.startswith("ORCHESTRATION_"):
                code = "AUTOMATION_CONFLICT"
            self._finish_failure(policy, run, code[:100])
        except Exception:
            self._finish_failure(policy, run, "AUTOMATION_EXECUTION_FAILED")

        self._audit_run(policy, run)
        self.session.flush()
        return run

    def _execution_tenant(self, policy: AutomationPolicy) -> TenantContext | None:
        user = self.session.get(User, policy.run_as_user_id)
        membership = self.session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == policy.organization_id,
                OrganizationMembership.user_id == policy.run_as_user_id,
            )
        )
        if user is None or not user.is_active or membership is None:
            return None
        permissions = ROLE_PERMISSIONS[membership.role]
        required = {
            Permission.RUN_PUBLIC_RESEARCH,
            Permission.RUN_FIT_ASSESSMENT,
            Permission.RUN_QUALIFICATION,
        }
        if policy.allow_provider_credits:
            required.add(Permission.SPEND_PROVIDER_CREDITS)
        if not required.issubset(permissions):
            return None
        return TenantContext(policy.organization_id, policy.run_as_user_id, membership.role)

    def _finish_failure(
        self,
        policy: AutomationPolicy,
        run: AutomationPolicyRun,
        error_code: str,
    ) -> None:
        run.status = AutomationPolicyRunStatus.FAILED
        run.error_code = error_code
        run.completed_at = self.now
        policy.last_status = run.status.value
        policy.last_error_code = error_code

    def _audit_run(self, policy: AutomationPolicy, run: AutomationPolicyRun) -> None:
        self.session.add(
            AuditLog(
                organization_id=policy.organization_id,
                actor_user_id=run.triggered_by_user_id or policy.run_as_user_id,
                action="automation.policy.run.completed",
                entity_type="automation_policy_run",
                entity_id=run.id,
                metadata_json={
                    "policy_id": policy.id,
                    "status": run.status.value,
                    "stop_reason": run.stop_reason,
                    "error_code": run.error_code,
                    "executed_steps": run.executed_steps,
                    "provider_credits_allowed": policy.allow_provider_credits,
                },
            )
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _next_run_after(scheduled_for: datetime, cadence_minutes: int, now: datetime) -> datetime:
    candidate = _as_utc(scheduled_for) + timedelta(minutes=cadence_minutes)
    current = _as_utc(now)
    cadence = timedelta(minutes=cadence_minutes)
    while candidate <= current:
        candidate += cadence
    return candidate
