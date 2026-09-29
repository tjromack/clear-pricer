import pytest

from clear_pricer.secrets import RedactedError, dsn_password, guard, redact

URL = "postgresql://postgres.abc:S3cr3t!pw@aws-0-us-east-2.pooler.supabase.com:5432/postgres"
KV = "host=localhost port=5433 dbname=clear_pricer user=clear_pricer password=clear_pricer_local"


def test_redacts_url_and_keyvalue_dsns():
    assert "S3cr3t" not in redact(f'Unable to connect to Postgres at "{URL}"')
    assert redact(URL) == "postgresql://postgres.abc:***@aws-0-us-east-2.pooler.supabase.com:5432/postgres"
    assert redact(KV).endswith("password=***")


def test_dsn_password():
    assert dsn_password(URL) == "S3cr3t!pw"
    assert dsn_password(KV) == "clear_pricer_local"


def test_guard_strips_password_from_driver_errors():
    """Regression: DuckDB's postgres extension echoes the full DSN (password included) when a connection fails."""
    with pytest.raises(RedactedError) as info:
        with guard(URL):
            raise OSError(f'IO Error: Unable to connect to Postgres at "{URL}": password authentication failed')
    assert "S3cr3t!pw" not in str(info.value) and "***" in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
