import sys

import pytest

from app.agents.resilience import reset_all_breakers


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Circuit breakers (app/agents/resilience.py) are deliberately
    process-global -- that's what lets one actually open after repeated
    failures instead of resetting every call. That same persistence would
    let one test's induced failures leak into the next test's (e.g. a test
    that trips anomaly.loki's failure_threshold would leave the breaker
    open for `reset_after_seconds`, silently short-circuiting a later
    test's "Loki call succeeds" case with a stale "circuit_open" failure).
    Reset before every test so each one starts from a clean, closed state.
    """
    reset_all_breakers()
    yield
    reset_all_breakers()


@pytest.fixture(autouse=True)
def _restore_app_dependency_overrides():
    """`app.main.app` is a process-global singleton shared by every test
    module, so a `dependency_overrides[...] = ...` that a test (or a module
    at import time) forgets to undo leaks into every test that runs after
    it -- and since pytest imports all test modules up front, "after" can
    even mean "all of them". Snapshot before each test and restore after,
    so an override can only ever affect the test that installed it.

    Looks the app up in `sys.modules` instead of importing it so that test
    modules that deliberately avoid pulling in app.main's heavy imports
    (they build their own one-router FastAPI app) stay light.
    """
    main = sys.modules.get("app.main")
    app = getattr(main, "app", None)
    if app is None:
        yield
        return
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def _make_account_headers(role: str) -> dict:
    """Creates a real account directly in the DB (there is no self-service
    signup -- accounts are admin-created, see routers/auth.py) and returns
    the `Authorization: Bearer <jwt>` header every real request carries.
    Same approach test_conversations_router.py / test_security_router.py use.
    The old shared `X-API-Key` header no longer authenticates anything: every
    business router sits behind `Depends(get_current_user)` (see main.py).
    """
    import uuid

    from app import crud
    from app.auth import create_access_token, hash_password
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        user = crud.create_user(
            db,
            username=f"test-{role}-{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("irrelevant-for-this-test"),
            role=role,
        )
        return {"Authorization": f"Bearer {create_access_token(user)}"}
    finally:
        db.close()


@pytest.fixture
def admin_headers() -> dict:
    return _make_account_headers("admin")


@pytest.fixture
def viewer_headers() -> dict:
    return _make_account_headers("viewer")


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    """CircuitBreaker.call sleeps RETRY_PAUSE_SECONDS (2s in production)
    before each retry. Every test that induces a failure against a retrying
    breaker would otherwise pay that per attempt -- and a test asserting a
    call "returns promptly" (test_resilience) would measure the pause
    instead of the timeout it is actually about."""
    from app.agents import resilience

    monkeypatch.setattr(resilience, "RETRY_PAUSE_SECONDS", 0.0)
