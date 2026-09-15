import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketingiq.domain.models import Base


@pytest.fixture
def session() -> Session:
    engine = create_engine(os.getenv("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    Base.metadata.drop_all(engine)
    engine.dispose()
