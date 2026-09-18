from sqlalchemy import create_engine, text

from marketingiq.infrastructure.schema_status import (
    expected_schema_heads,
    inspect_database_schema,
)


def test_expected_schema_head_tracks_latest_migration():
    assert expected_schema_heads() == ("20260918_18",)


def test_schema_status_reports_unmigrated_database_as_out_of_date():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    status = inspect_database_schema(engine)

    assert status.state == "schema_out_of_date"
    assert status.current_heads == ()
    assert status.expected_heads == ("20260918_18",)
    assert status.ready is False


def test_schema_status_reports_current_database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('20260918_18')")
        )

    status = inspect_database_schema(engine)

    assert status.state == "current"
    assert status.current_heads == ("20260918_18",)
    assert status.expected_heads == ("20260918_18",)
    assert status.ready is True
