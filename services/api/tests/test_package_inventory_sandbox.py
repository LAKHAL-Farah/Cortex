"""Phase Sec-3 (package-inventory collector) acceptance test.

The roadmap's acceptance criterion for this phase: "a real host returns
an actual matched/clean CVE finding instead of a degraded 'collector
unreachable' state." `cve_feed.match_cves` is already pure and already
covered by test_security_agent.py's unit tests; what's new here is that
`cve_feed.get_package_inventory` now has something real to talk to in the
sandbox -- infra/openstack-sim's own `/packages?host=` route (see its
app.py, "Phase Sec-3" section) -- instead of failing to connect.

This test loads that route directly (via FastAPI's TestClient over an
ASGI transport -- no live socket, no docker-compose needed) and feeds its
actual seeded response into the real, unmodified `match_cves`, proving the
full path end to end: sandbox seed data -> collector response shape ->
real CVE match/clean result. This is deliberately independent from
test_security_agent.py's `_check_cve_match` tests, which monkeypatch
`cve_feed.get_package_inventory` entirely and never touch the sandbox's
actual seed data -- this test is the one that would fail if the sim's
`/packages` route ever drifted from the shape `cve_feed.py` expects, or if
the seeded versions stopped actually straddling a known-vulnerable /
clean boundary.
"""
import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import cve_feed

SIM_APP_PATH = Path(__file__).resolve().parents[3] / "infra" / "openstack-sim" / "app.py"


@pytest.fixture(scope="module")
def sim_client():
    """Loads infra/openstack-sim/app.py as a standalone module (it isn't
    part of the `app` package -- it's a separate container's code, only
    ever imported here for an in-process test) and wraps it in a
    TestClient, same as any other FastAPI app under test."""
    spec = importlib.util.spec_from_file_location("openstack_sim_app", SIM_APP_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["openstack_sim_app"] = module
    spec.loader.exec_module(module)
    return TestClient(module.app)


def _packages_for(sim_client, hostname: str) -> list[dict]:
    response = sim_client.get("/packages", params={"host": hostname})
    assert response.status_code == 200
    return response.json()


def test_packages_route_returns_expected_shape(sim_client):
    packages = _packages_for(sim_client, "controller-sim")
    assert packages, "controller-sim should be seeded with packages"
    for pkg in packages:
        assert set(pkg.keys()) == {"name", "version"}
        assert isinstance(pkg["name"], str)
        assert isinstance(pkg["version"], str)


def test_unknown_host_returns_empty_list_not_error(sim_client):
    """A collector that's never heard of a host reports "nothing
    installed" -- an empty list -- not a 404/500. `_check_cve_match`
    should treat that as "no known-vulnerable packages found", a
    different, real state from "collector unreachable"."""
    assert _packages_for(sim_client, "no-such-host") == []


def test_controller_sim_has_a_real_vulnerable_finding(sim_client):
    """The acceptance criterion, made concrete: controller-sim's seeded
    openssh-server version is genuinely older than cve_feed._CVE_DATABASE's
    fixed_version, so match_cves -- the same pure function
    _check_cve_match calls -- returns a real match, not an empty list."""
    packages = _packages_for(sim_client, "controller-sim")
    matches = cve_feed.match_cves(packages)

    assert matches, "controller-sim's seeded openssh-server version should match a known CVE"
    match = next(m for m in matches if m["package"] == "openssh-server")
    assert match["cve_id"] == "CVE-2024-6387"
    assert match["installed_version"] == "9.6"
    assert match["severity"] == "critical"


@pytest.mark.parametrize("hostname", ["compute1-sim", "compute2-sim", "storage-sim"])
def test_other_nodes_are_seeded_clean(sim_client, hostname):
    """The rest of the fleet is seeded with versions comfortably newer
    than every fixed_version in _CVE_DATABASE, so a scan across the whole
    sandbox shows a real mix of vulnerable/clean rather than either
    extreme -- match_cves should find nothing for these hosts."""
    packages = _packages_for(sim_client, hostname)
    assert packages, f"{hostname} should still be seeded with packages"
    assert cve_feed.match_cves(packages) == []


def test_get_package_inventory_reads_the_sandbox_route_shape(sim_client, monkeypatch):
    """Confirms cve_feed.get_package_inventory (the real HTTP client
    _check_cve_match calls) needs zero changes to work against this route
    -- it's already a plain GET with a `host` query param expecting
    `[{"name": str, "version": str}, ...]`, exactly what /packages
    returns. Routes the client's `requests.get` through the TestClient
    instead of a real socket, since no live server is running in this
    test process."""

    def _fake_get(url, params=None, timeout=None):
        assert url == f"{cve_feed.PACKAGE_INVENTORY_URL}/packages"
        return sim_client.get("/packages", params=params)

    monkeypatch.setattr(cve_feed.requests, "get", _fake_get)

    packages = cve_feed.get_package_inventory("controller-sim")
    matches = cve_feed.match_cves(packages)
    assert any(m["package"] == "openssh-server" for m in matches)
