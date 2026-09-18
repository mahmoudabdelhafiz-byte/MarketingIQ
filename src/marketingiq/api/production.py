from __future__ import annotations

from fastapi import FastAPI

from marketingiq.api.app import create_app
from marketingiq.infrastructure.deployment_config import validate_deployment_environment


def create_production_app() -> FastAPI:
    """Construct the production ASGI app only after offline configuration validation."""

    report = validate_deployment_environment()
    if not report.ok:
        details = "; ".join(report.errors)
        raise RuntimeError(f"Invalid production deployment configuration: {details}")
    return create_app()
