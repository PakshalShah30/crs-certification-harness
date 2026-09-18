"""
Pure-Python fallback certification suite.

This mirrors, assertion-for-assertion, the pm.test(...) checks in
collection/crs-certification.postman_collection.json. It exists so the
certification logic can be proven correct with `pytest` even in
environments where Node.js / Newman is unavailable.

Tests for the three deliberate mock-API quirks (see mock_crs/app.py and
README.md) are marked `xfail(strict=True)`: we EXPECT them to fail against
the bundled, deliberately-imperfect mock. `strict=True` means if a quirk
ever gets fixed in the mock and the test starts passing, pytest reports an
XPASS failure -- which is exactly the signal you want ("hey, this quirk
doesn't reproduce anymore, update the fixture or the docs").

Run with:
    pytest -v
"""
from __future__ import annotations

import requests


# ---------------------------------------------------------------------------
# 1. System
# ---------------------------------------------------------------------------


def test_health(base_url):
    resp = requests.get(f"{base_url}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert resp.elapsed.total_seconds() < 2.0


# ---------------------------------------------------------------------------
# 2. Properties
# ---------------------------------------------------------------------------


def test_list_properties(base_url):
    resp = requests.get(f"{base_url}/properties")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["properties"], list)
    assert len(body["properties"]) > 0
    assert body["count"] == len(body["properties"])
    for prop in body["properties"]:
        assert isinstance(prop["property_id"], str)
        assert isinstance(prop["name"], str)
        assert isinstance(prop["city"], str)
        assert isinstance(prop["star_rating"], int)
        assert 1 <= prop["star_rating"] <= 5


# ---------------------------------------------------------------------------
# 3. Rates
# ---------------------------------------------------------------------------


def test_list_rates(base_url):
    resp = requests.get(f"{base_url}/rates")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rates"]) > 0
    assert body["count"] == len(body["rates"])
    for rate in body["rates"]:
        assert isinstance(rate["nightly_rate"], (int, float))
        assert rate["nightly_rate"] > 0  # business rule


def test_list_rates_unknown_property_is_404(base_url):
    resp = requests.get(f"{base_url}/rates", params={"property_id": "HTL-DOES-NOT-EXIST"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Availability
# ---------------------------------------------------------------------------


def test_availability_valid_range(base_url):
    resp = requests.get(
        f"{base_url}/availability",
        params={"property_id": "HTL-001", "checkin": "2026-11-01", "checkout": "2026-11-05"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["property_id"] == "HTL-001"
    assert body["checkin"] == "2026-11-01"
    assert body["checkout"] == "2026-11-05"
    assert isinstance(body["available"], bool)
    assert isinstance(body["rooms"], list)


def test_availability_checkout_before_checkin_is_rejected(base_url):
    """Contrast case: /availability correctly enforces date ordering.
    POST /reservations does not -- see the quirk test below."""
    resp = requests.get(
        f"{base_url}/availability",
        params={"property_id": "HTL-001", "checkin": "2026-11-05", "checkout": "2026-11-01"},
    )
    assert 400 <= resp.status_code <= 499


# ---------------------------------------------------------------------------
# 5. Reservations
# ---------------------------------------------------------------------------


def _valid_reservation_payload(**overrides):
    payload = {
        "property_id": "HTL-001",
        "rate_id": "RATE-STD-001",
        "room_type": "standard_queen",
        "checkin": "2026-12-10",
        "checkout": "2026-12-13",
        "guest_name": "Amara Okafor",
        "guest_email": "amara.okafor@example.com",
        "guests": 2,
    }
    payload.update(overrides)
    return payload


def test_create_reservation_valid(base_url):
    resp = requests.post(f"{base_url}/reservations", json=_valid_reservation_payload())
    assert resp.status_code == 201
    body = resp.json()

    required_fields = [
        "reservation_id", "property_id", "rate_id", "room_type", "checkin", "checkout",
        "guest_name", "guest_email", "guests", "nights", "nightly_rate", "currency",
        "total_amount", "status", "created_at", "updated_at",
    ]
    for field in required_fields:
        assert field in body, f"missing field {field}"

    assert body["total_amount"] == round(body["nightly_rate"] * body["nights"], 2)
    assert body["nightly_rate"] > 0
    assert body["status"] == "confirmed"


def test_create_then_get_round_trip(base_url):
    """Round-trip integrity: what you POST is exactly what a subsequent GET
    returns. Uses a well-formed guest_name, so this is expected to PASS --
    contrast with test_create_reservation_long_guest_name_is_truncated below."""
    payload = _valid_reservation_payload()
    create_resp = requests.post(f"{base_url}/reservations", json=payload)
    assert create_resp.status_code == 201
    reservation_id = create_resp.json()["reservation_id"]

    get_resp = requests.get(f"{base_url}/reservations/{reservation_id}")
    assert get_resp.status_code == 200
    body = get_resp.json()

    assert body["reservation_id"] == reservation_id
    assert body["guest_name"] == payload["guest_name"]
    assert body["checkin"] == payload["checkin"]
    assert body["checkout"] == payload["checkout"]
    assert body["guest_email"] == payload["guest_email"]


def test_get_unknown_reservation_is_404(base_url):
    resp = requests.get(f"{base_url}/reservations/RES-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_update_reservation_valid(base_url):
    create_resp = requests.post(f"{base_url}/reservations", json=_valid_reservation_payload())
    reservation_id = create_resp.json()["reservation_id"]

    update_resp = requests.put(
        f"{base_url}/reservations/{reservation_id}",
        json={"guest_name": "Amara Okafor-Whitfield"},
    )
    assert update_resp.status_code == 200
    body = update_resp.json()
    assert body["guest_name"] == "Amara Okafor-Whitfield"
    assert body["reservation_id"] == reservation_id


def test_cancel_reservation_valid(base_url):
    create_resp = requests.post(f"{base_url}/reservations", json=_valid_reservation_payload())
    reservation_id = create_resp.json()["reservation_id"]

    delete_resp = requests.delete(f"{base_url}/reservations/{reservation_id}")
    assert delete_resp.status_code == 200
    body = delete_resp.json()
    assert body["status"] == "cancelled"
    assert body["reservation_id"] == reservation_id


def test_cancel_unknown_reservation_correctly_returns_404(base_url):
    """Contrast case: DELETE handles unknown ids correctly, unlike PUT (see
    the QUIRK-2 test below)."""
    resp = requests.delete(f"{base_url}/reservations/RES-DOES-NOT-EXIST")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Deliberate quirks -- these are EXPECTED to fail against the bundled mock.
# Each xfail is strict: if the underlying mock is ever fixed, the test
# starts reporting XPASS (a failure), which is the correct signal to update
# this suite and the README.
# ---------------------------------------------------------------------------


import pytest  # noqa: E402  (kept near the xfail tests it documents)


@pytest.mark.xfail(
    strict=True,
    reason="CRS-QUIRK-1: mock accepts checkout before checkin instead of "
    "rejecting with a 4xx. See README.md 'Deliberate API quirks'.",
)
def test_create_reservation_rejects_checkout_before_checkin(base_url):
    payload = _valid_reservation_payload(checkin="2026-12-20", checkout="2026-12-18")
    resp = requests.post(f"{base_url}/reservations", json=payload)
    assert 400 <= resp.status_code <= 499, (
        f"Expected a 4xx validation error for checkout before checkin, but got "
        f"{resp.status_code}. Body: {resp.text}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="CRS-QUIRK-3: mock silently truncates guest_name over 50 chars "
    "instead of rejecting or preserving it. See README.md 'Deliberate API quirks'.",
)
def test_create_reservation_guest_name_round_trips_when_long(base_url):
    long_name = "Alexandra Christodoulopoulou-Featherstonehaugh the Third of Constantinople"
    assert len(long_name) > 50
    payload = _valid_reservation_payload(
        property_id="HTL-002", rate_id="RATE-STE-002", room_type="suite", guest_name=long_name,
        checkin="2026-12-01", checkout="2026-12-03",
    )
    resp = requests.post(f"{base_url}/reservations", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["guest_name"] == long_name, (
        f"Expected guest_name to round-trip exactly (length {len(long_name)}), but the "
        f"server truncated it to length {len(body['guest_name'])}."
    )


@pytest.mark.xfail(
    strict=True,
    reason="CRS-QUIRK-2: mock returns HTTP 200 with an error body for an "
    "update to an unknown reservation id, instead of HTTP 404. "
    "See README.md 'Deliberate API quirks'.",
)
def test_update_unknown_reservation_returns_404(base_url):
    resp = requests.put(f"{base_url}/reservations/RES-DOES-NOT-EXIST", json={"guest_name": "Nobody"})
    assert resp.status_code == 404, (
        f"Expected 404 for an update to a nonexistent reservation, but got {resp.status_code}. "
        f"Body: {resp.text}"
    )
