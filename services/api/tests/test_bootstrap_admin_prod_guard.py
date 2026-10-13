"""Production must never seed the default admin/admin account."""
import pytest

from app.main import InsecureBootstrapAdminError, _bootstrap_admin_credentials


def _env(monkeypatch, **kv):
    for k in ("CORTEX_ENV", "CORTEX_ADMIN_USERNAME", "CORTEX_ADMIN_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kv.items():
        monkeypatch.setenv(k, v)


def test_dev_keeps_default_admin(monkeypatch):
    _env(monkeypatch)
    assert _bootstrap_admin_credentials() == ("admin", "admin")


@pytest.mark.parametrize("pw", [None, "", "admin", " Admin "])
def test_production_rejects_unset_or_default_password(monkeypatch, pw):
    kv = {"CORTEX_ENV": "production"}
    if pw is not None:
        kv["CORTEX_ADMIN_PASSWORD"] = pw
    _env(monkeypatch, **kv)
    with pytest.raises(InsecureBootstrapAdminError):
        _bootstrap_admin_credentials()


def test_production_accepts_strong_password(monkeypatch):
    _env(monkeypatch, CORTEX_ENV="production", CORTEX_ADMIN_USERNAME="ops",
         CORTEX_ADMIN_PASSWORD="a-long-random-secret")
    assert _bootstrap_admin_credentials() == ("ops", "a-long-random-secret")
