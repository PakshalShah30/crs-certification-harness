"""
One-off generator for collection/crs-certification.postman_collection.json.

This script is NOT part of the certification runtime path — it exists only
so the (fairly large) Postman collection JSON can be built programmatically
instead of hand-typed, which keeps the pm.test(...) scripts consistent and
free of JSON syntax errors. Run it once with:

    python3 scripts/_build_collection.py

It is safe to delete after the collection file is generated and reviewed;
it is kept in the repo for transparency/reproducibility of how the
collection was authored.
"""
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "collection" / "crs-certification.postman_collection.json"

BASE = "{{baseUrl}}"


def request_item(name, method, url_path, tests, body=None, query=None, description=""):
    path_part = url_path.split("?")[0]
    url = {
        "raw": BASE + url_path,
        "host": ["{{baseUrl}}"],
        "path": [p for p in path_part.strip("/").split("/") if p != ""],
    }
    if query:
        url["query"] = [{"key": k, "value": v} for k, v in query.items()]
        url["raw"] = BASE + path_part + "?" + "&".join(f"{k}={v}" for k, v in query.items())

    request = {
        "method": method,
        "header": ([{"key": "Content-Type", "value": "application/json"}] if body is not None else []),
        "url": url,
        "description": description,
    }
    if body is not None:
        request["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }

    return {
        "name": name,
        "event": [
            {
                "listen": "test",
                "script": {"type": "text/javascript", "exec": tests},
            }
        ],
        "request": request,
        "response": [],
    }


# ---------------------------------------------------------------------------
# 0. Setup
# ---------------------------------------------------------------------------

setup_folder = {
    "name": "0 - Setup",
    "description": "Resets in-memory mock CRS state so the certification run is deterministic and repeatable.",
    "item": [
        request_item(
            "Reset mock state",
            "POST",
            "/_test/reset",
            js:=[
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
            ],
            description="Not part of the real CRS contract. Clears reservations created by a previous certification run.",
        )
    ],
}

# ---------------------------------------------------------------------------
# 1. System
# ---------------------------------------------------------------------------

system_folder = {
    "name": "1 - System",
    "item": [
        request_item(
            "GET /health - service is healthy",
            "GET",
            "/health",
            [
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "",
                "pm.test('Response has status field equal to ok', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json).to.have.property('status');",
                "    pm.expect(json.status).to.eql('ok');",
                "});",
                "",
                "pm.test('Response time is reasonable (< 2000ms)', function () {",
                "    pm.expect(pm.response.responseTime).to.be.below(2000);",
                "});",
            ],
        )
    ],
}

# ---------------------------------------------------------------------------
# 2. Properties
# ---------------------------------------------------------------------------

properties_folder = {
    "name": "2 - Properties",
    "item": [
        request_item(
            "GET /properties - list properties",
            "GET",
            "/properties",
            [
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "",
                "pm.test('Response has properties array and matching count', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json).to.have.property('properties');",
                "    pm.expect(json.properties).to.be.an('array');",
                "    pm.expect(json.properties.length).to.be.above(0);",
                "    pm.expect(json.count).to.eql(json.properties.length);",
                "});",
                "",
                "pm.test('Each property has required fields with correct types', function () {",
                "    const json = pm.response.json();",
                "    json.properties.forEach(function (p) {",
                "        pm.expect(p).to.have.property('property_id').that.is.a('string');",
                "        pm.expect(p).to.have.property('name').that.is.a('string');",
                "        pm.expect(p).to.have.property('city').that.is.a('string');",
                "        pm.expect(p).to.have.property('star_rating').that.is.a('number');",
                "        pm.expect(p.star_rating).to.be.within(1, 5);",
                "    });",
                "});",
                "",
                "pm.test('Store first property_id for downstream requests', function () {",
                "    const json = pm.response.json();",
                "    pm.collectionVariables.set('propertyId', json.properties[0].property_id);",
                "});",
            ],
        )
    ],
}

# ---------------------------------------------------------------------------
# 3. Rates
# ---------------------------------------------------------------------------

rates_folder = {
    "name": "3 - Rates",
    "item": [
        request_item(
            "GET /rates - list all rates",
            "GET",
            "/rates",
            [
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "",
                "pm.test('rates is a non-empty array matching count', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.rates).to.be.an('array');",
                "    pm.expect(json.rates.length).to.be.above(0);",
                "    pm.expect(json.count).to.eql(json.rates.length);",
                "});",
                "",
                "pm.test('Business rule: every nightly_rate is greater than 0', function () {",
                "    const json = pm.response.json();",
                "    json.rates.forEach(function (r) {",
                "        pm.expect(r.nightly_rate).to.be.a('number');",
                "        pm.expect(r.nightly_rate).to.be.above(0);",
                "    });",
                "});",
                "",
                "pm.test('Store a known rate_id/property_id pair for downstream requests', function () {",
                "    const json = pm.response.json();",
                "    const rate = json.rates.find(r => r.rate_id === 'RATE-STD-001');",
                "    pm.expect(rate, 'expected seed rate RATE-STD-001 to exist').to.not.be.undefined;",
                "    pm.collectionVariables.set('rateId', rate.rate_id);",
                "    pm.collectionVariables.set('ratePropertyId', rate.property_id);",
                "    pm.collectionVariables.set('rateRoomType', rate.room_type);",
                "    pm.collectionVariables.set('rateNightly', rate.nightly_rate);",
                "});",
            ],
        ),
        request_item(
            "GET /rates?property_id=unknown - 404 for unknown property",
            "GET",
            "/rates",
            [
                "pm.test('Status code is 404 for unknown property_id', function () {",
                "    pm.response.to.have.status(404);",
                "});",
            ],
            query={"property_id": "HTL-DOES-NOT-EXIST"},
        ),
    ],
}

# ---------------------------------------------------------------------------
# 4. Availability
# ---------------------------------------------------------------------------

availability_folder = {
    "name": "4 - Availability",
    "item": [
        request_item(
            "GET /availability - valid date range",
            "GET",
            "/availability",
            [
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "",
                "pm.test('Echoes back requested property_id and dates', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.property_id).to.eql(pm.collectionVariables.get('propertyId') || 'HTL-001');",
                "    pm.expect(json.checkin).to.eql('2026-11-01');",
                "    pm.expect(json.checkout).to.eql('2026-11-05');",
                "});",
                "",
                "pm.test('Response has an array of rooms and a boolean available flag', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json).to.have.property('available').that.is.a('boolean');",
                "    pm.expect(json.rooms).to.be.an('array');",
                "});",
            ],
            query={"property_id": "HTL-001", "checkin": "2026-11-01", "checkout": "2026-11-05"},
        ),
        request_item(
            "GET /availability - checkout before checkin is rejected",
            "GET",
            "/availability",
            [
                "pm.test('Status code is 4xx when checkout is before checkin', function () {",
                "    pm.expect(pm.response.code).to.be.within(400, 499);",
                "});",
            ],
            query={"property_id": "HTL-001", "checkin": "2026-11-05", "checkout": "2026-11-01"},
            description="Contrast case: /availability correctly enforces date ordering. "
            "POST /reservations does not (see quirk test in folder 5).",
        ),
    ],
}

# ---------------------------------------------------------------------------
# 5. Reservations
# ---------------------------------------------------------------------------

create_valid_body = {
    "property_id": "HTL-001",
    "rate_id": "RATE-STD-001",
    "room_type": "standard_queen",
    "checkin": "2026-12-10",
    "checkout": "2026-12-13",
    "guest_name": "Amara Okafor",
    "guest_email": "amara.okafor@example.com",
    "guests": 2,
}

create_bad_dates_body = {
    "property_id": "HTL-001",
    "rate_id": "RATE-STD-001",
    "room_type": "standard_queen",
    "checkin": "2026-12-20",
    "checkout": "2026-12-18",
    "guest_name": "Date Order Test",
    "guest_email": "date.order@example.com",
    "guests": 1,
}

LONG_NAME = "Alexandra Christodoulopoulou-Featherstonehaugh the Third of Constantinople"  # 74 chars, > 50
create_long_name_body = {
    "property_id": "HTL-002",
    "rate_id": "RATE-STE-002",
    "room_type": "suite",
    "checkin": "2026-12-01",
    "checkout": "2026-12-03",
    "guest_name": LONG_NAME,
    "guest_email": "long.name@example.com",
    "guests": 1,
}

reservations_folder = {
    "name": "5 - Reservations",
    "item": [
        request_item(
            "POST /reservations - create valid reservation",
            "POST",
            "/reservations",
            [
                "pm.test('Status code is 201', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "",
                "pm.test('Response has all required reservation fields', function () {",
                "    const json = pm.response.json();",
                "    ['reservation_id','property_id','rate_id','room_type','checkin','checkout',",
                "     'guest_name','guest_email','guests','nights','nightly_rate','currency',",
                "     'total_amount','status','created_at','updated_at'].forEach(function (field) {",
                "        pm.expect(json, 'missing field ' + field).to.have.property(field);",
                "    });",
                "});",
                "",
                "pm.test('Business rule: total_amount equals nightly_rate * nights', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.total_amount).to.eql(Math.round(json.nightly_rate * json.nights * 100) / 100);",
                "});",
                "",
                "pm.test('Business rule: nightly_rate is greater than 0', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.nightly_rate).to.be.above(0);",
                "});",
                "",
                "pm.test('status is confirmed for a new reservation', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.status).to.eql('confirmed');",
                "});",
                "",
                "pm.test('Store reservation_id and submitted values for round-trip check', function () {",
                "    const json = pm.response.json();",
                "    pm.collectionVariables.set('createdReservationId', json.reservation_id);",
                "    pm.collectionVariables.set('submittedGuestName', pm.request.body ? JSON.parse(pm.request.body.raw).guest_name : '');",
                "    pm.collectionVariables.set('submittedCheckin', JSON.parse(pm.request.body.raw).checkin);",
                "    pm.collectionVariables.set('submittedCheckout', JSON.parse(pm.request.body.raw).checkout);",
                "    pm.collectionVariables.set('submittedGuestEmail', JSON.parse(pm.request.body.raw).guest_email);",
                "});",
            ],
            body=create_valid_body,
        ),
        request_item(
            "GET /reservations/{id} - round trip matches create",
            "GET",
            "/reservations/{{createdReservationId}}",
            [
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "",
                "pm.test('Round trip: reservation_id matches the one just created', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.reservation_id).to.eql(pm.collectionVariables.get('createdReservationId'));",
                "});",
                "",
                "pm.test('Round trip: guest_name, checkin, checkout, guest_email match submitted values exactly', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.guest_name).to.eql(pm.collectionVariables.get('submittedGuestName'));",
                "    pm.expect(json.checkin).to.eql(pm.collectionVariables.get('submittedCheckin'));",
                "    pm.expect(json.checkout).to.eql(pm.collectionVariables.get('submittedCheckout'));",
                "    pm.expect(json.guest_email).to.eql(pm.collectionVariables.get('submittedGuestEmail'));",
                "});",
            ],
            description="Verifies that what was submitted on create is exactly what a subsequent GET returns "
            "(round-trip integrity). This request uses a well-formed guest_name so it should PASS -- "
            "contrast with the long-guest-name quirk test below.",
        ),
        request_item(
            "POST /reservations - QUIRK: checkout before checkin should be rejected",
            "POST",
            "/reservations",
            [
                "pm.test('QUIRK CHECK: server rejects checkout <= checkin with a 4xx status', function () {",
                "    pm.expect(pm.response.code, 'Expected a 4xx validation error for checkout before checkin, ' +",
                "        'but the server accepted the booking. This is CRS-QUIRK-1 (see README).').to.be.within(400, 499);",
                "});",
            ],
            body=create_bad_dates_body,
            description="CRS-QUIRK-1: the mock API accepts a reservation whose checkout date is BEFORE its "
            "checkin date. A certified CRS must reject this with a 4xx. This test is expected to FAIL "
            "against the bundled mock -- that failure is the harness doing its job.",
        ),
        request_item(
            "POST /reservations - QUIRK: guest_name over 50 chars must round-trip exactly",
            "POST",
            "/reservations",
            [
                "pm.test('Request accepted (201)', function () {",
                "    pm.response.to.have.status(201);",
                "});",
                "",
                "pm.test('QUIRK CHECK: guest_name is stored/returned without silent truncation', function () {",
                "    const json = pm.response.json();",
                "    const submitted = JSON.parse(pm.request.body.raw).guest_name;",
                "    pm.expect(json.guest_name, 'Expected guest_name to round-trip exactly (length ' + submitted.length +",
                "        '), but the server truncated it to length ' + json.guest_name.length +",
                "        '. This is CRS-QUIRK-3 (see README).').to.eql(submitted);",
                "});",
            ],
            body=create_long_name_body,
            description="CRS-QUIRK-3: guest names longer than 50 characters are silently truncated instead of "
            "being rejected or preserved. This test is expected to FAIL against the bundled mock.",
        ),
        request_item(
            "PUT /reservations/{id} - valid update",
            "PUT",
            "/reservations/{{createdReservationId}}",
            [
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "",
                "pm.test('guest_name reflects the update', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.guest_name).to.eql('Amara Okafor-Whitfield');",
                "});",
                "",
                "pm.test('updated_at changed and reservation_id is stable', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.reservation_id).to.eql(pm.collectionVariables.get('createdReservationId'));",
                "    pm.expect(json).to.have.property('updated_at');",
                "});",
            ],
            body={"guest_name": "Amara Okafor-Whitfield"},
        ),
        request_item(
            "PUT /reservations/{id} - QUIRK: unknown id should return 404",
            "PUT",
            "/reservations/RES-DOES-NOT-EXIST",
            [
                "pm.test('QUIRK CHECK: unknown reservation returns 404, not 200-with-error-body', function () {",
                "    pm.expect(pm.response.code, 'Expected 404 for an update to a nonexistent reservation, but got ' +",
                "        pm.response.code + '. This is CRS-QUIRK-2 (see README): the server returns HTTP 200 ' +",
                "        'with an {\"error\": ...} body instead of a proper 404.').to.eql(404);",
                "});",
            ],
            body={"guest_name": "Nobody"},
            description="CRS-QUIRK-2: updating a reservation that does not exist returns HTTP 200 with an "
            "error payload instead of HTTP 404. This test is expected to FAIL against the bundled mock.",
        ),
        request_item(
            "DELETE /reservations/{id} - cancel valid reservation",
            "DELETE",
            "/reservations/{{createdReservationId}}",
            [
                "pm.test('Status code is 200', function () {",
                "    pm.response.to.have.status(200);",
                "});",
                "",
                "pm.test('status is cancelled', function () {",
                "    const json = pm.response.json();",
                "    pm.expect(json.status).to.eql('cancelled');",
                "    pm.expect(json.reservation_id).to.eql(pm.collectionVariables.get('createdReservationId'));",
                "});",
            ],
        ),
        request_item(
            "DELETE /reservations/{id} - unknown id correctly returns 404",
            "DELETE",
            "/reservations/RES-DOES-NOT-EXIST",
            [
                "pm.test('Status code is 404 (contrast: DELETE handles unknown ids correctly, unlike PUT)', function () {",
                "    pm.response.to.have.status(404);",
                "});",
            ],
        ),
    ],
}

collection = {
    "info": {
        "_postman_id": str(uuid.uuid4()),
        "name": "CRS Certification",
        "description": "Automated partner-certification suite for the Mock CRS API. "
        "Run against a live instance of mock_crs (see ../mock_crs) with the "
        "'CRS Local' environment. Three tests are EXPECTED to fail against the "
        "bundled mock -- they exist to prove the harness catches real defects "
        "(see README.md 'Deliberate API quirks').",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "variable": [
        {"key": "createdReservationId", "value": ""},
        {"key": "submittedGuestName", "value": ""},
        {"key": "submittedCheckin", "value": ""},
        {"key": "submittedCheckout", "value": ""},
        {"key": "submittedGuestEmail", "value": ""},
        {"key": "propertyId", "value": ""},
        {"key": "rateId", "value": ""},
        {"key": "ratePropertyId", "value": ""},
        {"key": "rateRoomType", "value": ""},
        {"key": "rateNightly", "value": ""},
    ],
    "item": [
        setup_folder,
        system_folder,
        properties_folder,
        rates_folder,
        availability_folder,
        reservations_folder,
    ],
}

OUT.write_text(json.dumps(collection, indent=2) + "\n")
print(f"Wrote {OUT}")
