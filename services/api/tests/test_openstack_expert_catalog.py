"""Invariant tests for openstack_expert_catalog.CATALOG -- these aren't
testing behavior, they're testing that the *data* keeps the promises
openstack_expert.py's rendering code and the v0.6 DoD both depend on:
every confirm_command is genuinely read-only, every command is labeled,
every entry has something in both layers, and ids are unique (the agent
looks entries up by id).
"""
from app.agents.nodes.openstack_expert_catalog import CATALOG

_VALID_CATEGORIES = {
    "compute", "storage", "network", "identity", "image",
    "message-bus", "database", "hypervisor", "host",
}


def test_catalog_is_not_trivially_small():
    # "a lot of curated entries" -- not a magic number, just a floor that
    # would fail loudly if someone accidentally truncated the list.
    assert len(CATALOG) >= 12


def test_every_entry_id_is_unique():
    ids = [e["id"] for e in CATALOG]
    assert len(ids) == len(set(ids))


def test_every_entry_has_required_fields_and_valid_category():
    required = {
        "id", "title", "category", "metric_names", "service_binaries",
        "keywords", "what_it_means", "confirm_commands",
        "remediation_commands", "doc_ref",
    }
    for entry in CATALOG:
        missing = required - entry.keys()
        assert not missing, f"{entry.get('id')} missing fields: {missing}"
        assert entry["category"] in _VALID_CATEGORIES, entry["id"]


def test_every_entry_has_at_least_one_confirm_and_one_remediation_command():
    for entry in CATALOG:
        assert entry["confirm_commands"], f"{entry['id']} has no confirm_commands"
        assert entry["remediation_commands"], f"{entry['id']} has no remediation_commands"


def test_confirm_commands_are_always_read_only():
    """The core safety invariant: layer 2 ("how to confirm it yourself")
    must never suggest a state-changing command -- that's what layer 3 is
    for. A single violation here would mean this agent could tell someone
    a destructive command is safe to "just check" with.
    """
    violations = [
        (entry["id"], cmd["command"])
        for entry in CATALOG
        for cmd in entry["confirm_commands"]
        if not cmd["read_only"]
    ]
    assert violations == []


def test_every_command_has_a_read_only_bool_and_nonempty_description():
    for entry in CATALOG:
        for cmd in entry["confirm_commands"] + entry["remediation_commands"]:
            assert isinstance(cmd["read_only"], bool), (entry["id"], cmd["command"])
            assert cmd["description"].strip(), (entry["id"], cmd["command"])
            assert cmd["command"].strip(), entry["id"]


def test_every_remediation_section_has_at_least_one_state_changing_command():
    # If every remediation command were read-only, it wouldn't actually be
    # a remediation section -- it'd just be more confirmation. Not a hard
    # technical requirement, but a real one for the catalog's own promise
    # ("what's usually done about it" implies actually *doing* something).
    for entry in CATALOG:
        assert any(not cmd["read_only"] for cmd in entry["remediation_commands"]), entry["id"]


def test_sprint1_scored_metrics_are_covered():
    # anomaly_detector.py's METRICS dict -- the two metrics Sprint 1
    # anomaly detection actually flags today -- must each have a matching
    # catalog entry, since these are the most likely real triggers.
    covered_metrics = {m for e in CATALOG for m in e["metric_names"]}
    assert "cpu_usage" in covered_metrics
    assert "ram_usage" in covered_metrics


def test_topology_synced_service_binaries_are_covered():
    # The exact binaries topology_sync.py syncs (see its tests) -- each
    # should have a matching catalog entry so a Service.state ==
    # "unreachable" finding for any of them can actually be explained.
    covered_binaries = {b for e in CATALOG for b in e["service_binaries"]}
    for binary in [
        "nova-compute", "nova-scheduler", "cinder-volume",
        "neutron-dhcp-agent", "neutron-l3-agent", "neutron-openvswitch-agent",
    ]:
        assert binary in covered_binaries, binary
