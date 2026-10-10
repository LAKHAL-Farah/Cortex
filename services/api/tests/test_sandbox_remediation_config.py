"""
services/api/tests/test_sandbox_remediation_config.py

Two guards for the sandbox remediation flow that the other remediation tests
cannot catch, because they build their own fixtures instead of reading the
files docker-compose actually mounts:

1. Every sandbox clouds.yaml defines the profile the executor connects with.
   (A missing `cortex-operator` made every OpenStack-SDK fix fail instantly with
   "The action failed before it could finish": `openstack.connect` raised
   `ConfigException: Cloud cortex-operator was not found`, which `run_plan`
   reports as a generic failure -- while the unit tests, which monkeypatch
   `_connect`, stayed green.)
2. The sandbox-only "Approve & execute" switch can never be on in production.
"""
from pathlib import Path

import pytest
import yaml

from app.services import remediation_executor as executor

SIM = Path(__file__).resolve().parents[3] / "infra" / "openstack-sim"
COMPOSE_SANDBOX = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.sandbox.yml"
CLOUDS_FILES = [
    SIM / "config" / "clouds.yaml",          # the one docker-compose.sandbox.yml mounts at /etc/openstack
    SIM / "etc-openstack" / "clouds.yaml",
    SIM / "clouds.sandbox.yaml",
]


@pytest.mark.parametrize("path", CLOUDS_FILES, ids=lambda p: str(p.relative_to(SIM)))
def test_sandbox_clouds_yaml_has_reader_and_operator(path):
    clouds = yaml.safe_load(path.read_text())["clouds"]
    assert "cortex-reader" in clouds
    assert executor.OS_REMEDIATION_CLOUD in clouds, (
        f"{path.name} has no '{executor.OS_REMEDIATION_CLOUD}' profile: every OpenStack-SDK fix would fail "
        "before it reached the simulator."
    )
    # Distinct identities, or the sim could not tell the operator from the reader.
    assert clouds[executor.OS_REMEDIATION_CLOUD]["auth"]["username"] == "cortex-operator"
    assert clouds["cortex-reader"]["auth"]["username"] == "cortex-reader"


def test_the_mounted_directory_is_the_one_that_was_fixed():
    compose = COMPOSE_SANDBOX.read_text()
    assert "./openstack-sim/config:/etc/openstack:ro" in compose


@pytest.mark.parametrize(
    "env, flag, expected",
    [
        ("sandbox", None, True),
        ("sandbox", "off", False),
        ("development", None, False),
        ("development", "on", True),
        ("production", None, False),
        ("production", "on", False),  # never, whatever the variable says
    ],
)
def test_one_click_is_sandbox_only_and_never_production(monkeypatch, env, flag, expected):
    monkeypatch.setattr(executor, "CORTEX_ENV", env)
    if flag is None:
        monkeypatch.delenv("CORTEX_REMEDIATION_ONE_CLICK", raising=False)
    else:
        monkeypatch.setenv("CORTEX_REMEDIATION_ONE_CLICK", flag)
    assert executor.one_click_enabled() is expected
