from wta_common.logging import _redact


def test_redacts_sensitive_keys() -> None:
    event = {"event": "x", "api_key": "abc", "Authorization": "Bearer t", "ok": 1}
    out = _redact(None, "info", event)
    assert out["api_key"] == "***"
    assert out["Authorization"] == "***"
    assert out["ok"] == 1
