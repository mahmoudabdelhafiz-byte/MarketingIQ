import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set")

    options: dict[str, object] = {"pool_pre_ping": True}
    if make_url(url).get_backend_name() == "mysql":
        raw_recycle = os.environ.get("DB_POOL_RECYCLE_SECONDS", "280")
        try:
            recycle = int(raw_recycle)
        except ValueError as error:
            raise RuntimeError("DB_POOL_RECYCLE_SECONDS must be an integer") from error
        if recycle <= 0:
            raise RuntimeError("DB_POOL_RECYCLE_SECONDS must be greater than zero")
        options["pool_recycle"] = recycle

    return create_engine(url, **options)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
