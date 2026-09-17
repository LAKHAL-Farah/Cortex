"""Tests for app/services/keystone_audit.py -- Phase Sec-5c's fleet-wide
Keystone token-abuse detection. `find_abusive_token_patterns` is pure (no
network calls), so these tests exercise it directly with hand-built
issuance-event fixtures, the same style test_exposed_ports.py uses for
exposed_ports.py's own pure matcher.
"""
from datetime import datetime, timedelta, timezone

from app.services import keystone_audit


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _event(username="alice", project="sandbox-project", source_ip="10.0.0.5", issued_at=None, ttl_seconds=3600):
    issued_at = issued_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "username": username,
        "project": project,
        "source_ip": source_ip,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(issued_at + timedelta(seconds=ttl_seconds)),
    }


def test_clean_log_flags_nothing(monkeypatch):
    monkeypatch.setenv("CORTEX_KEYSTONE_EXPECTED_CIDRS", "")
    events = [_event(issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc))]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["rapid_reissue"] == []
    assert result["long_lived"] == []
    assert result["unexpected_ip_checked"] is False


def test_rapid_reissue_flagged_when_same_user_reissues_within_window():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        _event(username="alice", issued_at=base),
        _event(username="alice", issued_at=base + timedelta(seconds=2)),
        _event(username="alice", issued_at=base + timedelta(seconds=4)),
    ]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert len(result["rapid_reissue"]) == 1
    assert result["rapid_reissue"][0]["username"] == "alice"
    assert result["rapid_reissue"][0]["count"] == 3


def test_rapid_reissue_not_flagged_when_spread_across_a_wider_window():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        _event(username="bob", issued_at=base),
        _event(username="bob", issued_at=base + timedelta(minutes=5)),
        _event(username="bob", issued_at=base + timedelta(minutes=10)),
    ]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["rapid_reissue"] == []


def test_rapid_reissue_is_per_user_not_across_the_whole_fleet():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        _event(username="alice", issued_at=base),
        _event(username="bob", issued_at=base + timedelta(seconds=1)),
        _event(username="carol", issued_at=base + timedelta(seconds=2)),
    ]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["rapid_reissue"] == []


def test_unexpected_ip_not_checked_when_no_cidrs_configured(monkeypatch):
    monkeypatch.setenv("CORTEX_KEYSTONE_EXPECTED_CIDRS", "")
    events = [_event(source_ip="203.0.113.10")]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["unexpected_ip_checked"] is False
    assert result["unexpected_ip"] == []


def test_unexpected_ip_flagged_when_outside_configured_cidrs(monkeypatch):
    monkeypatch.setenv("CORTEX_KEYSTONE_EXPECTED_CIDRS", "10.0.0.0/8")
    events = [_event(source_ip="203.0.113.10", username="mallory")]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["unexpected_ip_checked"] is True
    assert len(result["unexpected_ip"]) == 1
    assert result["unexpected_ip"][0]["username"] == "mallory"


def test_expected_ip_not_flagged_when_inside_configured_cidrs(monkeypatch):
    monkeypatch.setenv("CORTEX_KEYSTONE_EXPECTED_CIDRS", "10.0.0.0/8")
    events = [_event(source_ip="10.0.0.42", username="alice")]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["unexpected_ip"] == []


def test_invalid_cidr_in_env_is_ignored_not_fatal(monkeypatch):
    monkeypatch.setenv("CORTEX_KEYSTONE_EXPECTED_CIDRS", "not-a-cidr, 10.0.0.0/8")
    events = [_event(source_ip="10.0.0.42")]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["unexpected_ip"] == []


def test_long_lived_token_flagged_above_the_ttl_threshold():
    events = [_event(username="alice", ttl_seconds=24 * 60 * 60)]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert len(result["long_lived"]) == 1
    assert result["long_lived"][0]["username"] == "alice"


def test_normal_ttl_not_flagged():
    events = [_event(ttl_seconds=3600)]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["long_lived"] == []


def test_malformed_timestamps_are_skipped_not_fatal():
    events = [{"username": "alice", "project": "p", "source_ip": "10.0.0.5", "issued_at": "not-a-date", "expires_at": "also-not-a-date"}]
    result = keystone_audit.find_abusive_token_patterns(events)
    assert result["long_lived"] == []
    assert result["rapid_reissue"] == []
    assert result["event_count"] == 1
