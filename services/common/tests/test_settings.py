import pytest
from wta_common.settings import ConfigError, Settings


def make(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_defaults_are_development_and_not_no_auth() -> None:
    s = make()
    assert s.app_env == "development"
    assert s.local_no_auth is False


def test_no_auth_in_production_fails_closed() -> None:
    with pytest.raises(ConfigError, match="development-only"):
        make(app_env="production", local_no_auth=True)


def test_no_auth_allowed_in_development() -> None:
    assert make(app_env="development", local_no_auth=True).local_no_auth


def test_secrets_not_in_repr() -> None:
    s = make(anthropic_api_key="sk-secret-value")
    assert "sk-secret-value" not in repr(s)


def test_admin_cors_origin_list_parsing() -> None:
    assert make(admin_cors_origins=" http://a.test , ,http://b.test").admin_cors_origin_list == [
        "http://a.test",
        "http://b.test",
    ]
