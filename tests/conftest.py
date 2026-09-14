import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from marketingiq.domain.models import Base


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
