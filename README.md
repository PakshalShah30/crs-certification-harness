# CRS Certification Harness

**One command turns a hotel CRS partner-certification cycle into an audit-ready pass/fail report — fully offline.**

## The problem

Before a channel partner (an OTA, a booking engine, a GDS connection) goes
live against a hotel Central Reservation System, it has to pass a
*certification cycle*: someone runs a checklist of API calls against the
partner's implementation and writes down what passed and what didn't. In a
lot of the industry this is still:

- a spreadsheet of test cases,
- a person clicking through Postman by hand,
- "it works on my side" as the final word on whether a defect is real,
- and a sign-off document that gets typed up from memory after the fact.

That doesn't scale, isn't reproducible, and produces no evidence trail. This
repo replaces that with a harness that runs the same test suite the same way
every time, against a contract (an OpenAPI spec), and emits a report file
that says exactly what was tested, what passed, what failed, and why —
generated from real test output, not written by hand.

## Architecture

Everything runs locally, offline, with no external services and no secrets.

```mermaid
flowchart LR
    subgraph "Contract"
        Spec["spec/crs-openapi.yaml<br/>(OpenAPI 3.0)"]
    end

    subgraph "System under test"
        Mock["mock_crs/app.py<br/>(FastAPI, in-memory)"]
    end

    subgraph "Certification suite"
        Coll["collection/crs-certification.postman_collection.json<br/>(Postman v2.1, pm.test assertions)"]
    end

    subgraph "Runner"
        Runner["run_certification.sh /<br/>scripts/run_certification.py"]
    end

    Newman["Newman<br/>(Postman CLI runner)"]
    PyFallback["scripts/run_python_tests.py<br/>+ pytest (no Node required)"]

    Signoff["scripts/generate_signoff.py"]
    Report["reports/certification-report.md<br/>+ .html"]

    Spec -.describes contract for.-> Mock
    Spec -.describes contract for.-> Coll
    Runner -->|starts, health-checks| Mock
    Runner -->|runs| Newman
    Newman -->|executes| Coll
    Coll -->|HTTP requests| Mock
    Newman -->|newman-report.json| Signoff
    Signoff --> Report
    PyFallback -.same assertions, no Node.-> Mock
```

Two independent, equivalent test paths exist on purpose:

1. **Newman path** (`run_certification.sh`) — runs the real Postman
   collection with the real Postman CLI runner. This is what you'd actually
   hand a partner: a `.postman_collection.json` they can also open in the
   Postman GUI and inspect.
2. **Python fallback path** (`scripts/run_python_tests.py`) — the same
   business-rule assertions re-implemented as `pytest` tests using
   `requests`, for environments where Node.js/Newman isn't available (e.g. a
   minimal CI runner, or to prove the logic is correct independent of
   Newman's JS sandbox).

## Deliberate API quirks

The bundled mock CRS (`mock_crs/app.py`) is not a perfect reference
implementation — it has three defects modeled on real, reported CRS
certification failures. The harness is designed to catch exactly these:

| # | Quirk | Where | Caught by |
|---|---|---|---|
| **CRS-QUIRK-1** | `POST /reservations` accepts a booking whose `checkout` date is before (or equal to) `checkin`, instead of rejecting it with a 4xx. | `mock_crs/app.py::create_reservation` | Postman: *"POST /reservations - QUIRK: checkout before checkin should be rejected"* · pytest: `test_create_reservation_rejects_checkout_before_checkin` |
| **CRS-QUIRK-2** | `PUT /reservations/{id}` returns **HTTP 200** with an `{"error": ...}` body when the reservation doesn't exist, instead of **HTTP 404**. This is one of the most common real-world certification failures — callers who only check `response.ok` never notice the update silently failed. | `mock_crs/app.py::update_reservation` | Postman: *"PUT /reservations/{id} - QUIRK: unknown id should return 404"* · pytest: `test_update_unknown_reservation_returns_404` |
| **CRS-QUIRK-3** | `guest_name` values longer than 50 characters are **silently truncated** instead of being rejected (or preserved). The client believes the booking was created faithfully; the stored guest name is corrupted. | `mock_crs/app.py::create_reservation` (`GUEST_NAME_MAX_LEN`) | Postman: *"POST /reservations - QUIRK: guest_name over 50 chars must round-trip exactly"* · pytest: `test_create_reservation_guest_name_round_trips_when_long` |

Two contrast cases are included deliberately so the report shows the harness
isn't just failing everything: `/availability` **correctly** rejects
`checkout <= checkin` with a 422, and `DELETE /reservations/{id}` **correctly**
returns 404 for an unknown id. Only the specific quirky code paths fail.

## Quick start (5 minutes)

```bash
git clone <this-repo>
cd crs-certification-harness

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Option A: full path with Newman (needs Node.js)
npm install newman newman-reporter-htmlextra
./run_certification.sh

# Option B: pure-Python fallback (no Node.js needed)
python3 scripts/run_python_tests.py
```

`run_certification.sh` starts the mock API, waits for `/health`, resets its
state, runs the Postman collection with Newman, stops the API, and writes
`reports/certification-report.md` (+ `.html`). Its exit code mirrors
Newman's — **non-zero is expected** against the bundled mock, because it has
the three deliberate defects above. That non-zero exit *is the harness
working correctly.*

You can also run the mock API standalone and poke at it:

```bash
uvicorn mock_crs.app:app --reload --port 8000
curl http://localhost:8000/health
open http://localhost:8000/docs   # interactive Swagger UI (FastAPI autodocs)
```

## Real sample output

### Newman run (`./run_certification.sh`)

```
❏ 5 - Reservations
↳ POST /reservations - create valid reservation
  POST http://localhost:8000/reservations [201 Created, 552B, 36ms]
  ✓  Status code is 201
  ✓  Response has all required reservation fields
  ✓  Business rule: total_amount equals nightly_rate * nights
  ✓  status is confirmed for a new reservation

↳ POST /reservations - QUIRK: checkout before checkin should be rejected
  POST http://localhost:8000/reservations [201 Created, 551B, 3ms]
  1. QUIRK CHECK: server rejects checkout <= checkin with a 4xx status

↳ PUT /reservations/{id} - QUIRK: unknown id should return 404
  PUT http://localhost:8000/reservations/RES-DOES-NOT-EXIST [200 OK, 217B, 2ms]
  3. QUIRK CHECK: unknown reservation returns 404, not 200-with-error-body

┌─────────────────────────┬──────────────────┬─────────────────┐
│                         │         executed │          failed │
├─────────────────────────┼──────────────────┼─────────────────┤
│              iterations │                1 │               0 │
│                requests │               15 │               0 │
│            test-scripts │               15 │               0 │
│              assertions │               36 │               3 │
└─────────────────────────┴──────────────────┴─────────────────┘

  #  failure         detail
 1.  AssertionError  QUIRK CHECK: server rejects checkout <= checkin with a 4xx status
                     Expected a 4xx validation error for checkout before checkin, but the
                     server accepted the booking. This is CRS-QUIRK-1 (see README).:
                     expected 201 to be within 400..499
 2.  AssertionError  QUIRK CHECK: guest_name is stored/returned without silent truncation
                     Expected guest_name to round-trip exactly (length 74), but the server
                     truncated it to length 50. This is CRS-QUIRK-3 (see README).
 3.  AssertionError  QUIRK CHECK: unknown reservation returns 404, not 200-with-error-body
                     Expected 404 for an update to a nonexistent reservation, but got 200.
                     This is CRS-QUIRK-2 (see README): the server returns HTTP 200 with an
                     {"error": ...} body instead of a proper 404.
```

The full, real, generated report from this exact run is committed at
[`reports/certification-report.md`](reports/certification-report.md) (and
[`reports/certification-report.html`](reports/certification-report.html)).
Excerpt:

```
## Verdict: FAIL

3 assertion failure(s) were observed, all attributable to documented defects
in the system under test (see 'Known quirks caught' below). The
certification harness is working as designed: it caught real, reproducible
API defects. The system under test does NOT pass certification until these
are fixed.

## Summary

| Metric | Value |
|---|---|
| Requests executed | 15 |
| Requests with a failure | 0 |
| Assertions executed | 36 |
| Assertions passed | 33 |
| Assertions failed | 3 |
| Pass rate | 91.7% |
```

### Python fallback run (`python3 scripts/run_python_tests.py`)

```
tests/test_certification.py::test_health PASSED                          [  6%]
tests/test_certification.py::test_list_properties PASSED                 [ 13%]
tests/test_certification.py::test_list_rates PASSED                      [ 20%]
tests/test_certification.py::test_list_rates_unknown_property_is_404 PASSED [ 26%]
tests/test_certification.py::test_availability_valid_range PASSED        [ 33%]
tests/test_certification.py::test_availability_checkout_before_checkin_is_rejected PASSED [ 40%]
tests/test_certification.py::test_create_reservation_valid PASSED        [ 46%]
tests/test_certification.py::test_create_then_get_round_trip PASSED      [ 53%]
tests/test_certification.py::test_get_unknown_reservation_is_404 PASSED  [ 60%]
tests/test_certification.py::test_update_reservation_valid PASSED        [ 66%]
tests/test_certification.py::test_cancel_reservation_valid PASSED        [ 73%]
tests/test_certification.py::test_cancel_unknown_reservation_correctly_returns_404 PASSED [ 80%]
tests/test_certification.py::test_create_reservation_rejects_checkout_before_checkin XFAIL [ 86%]
tests/test_certification.py::test_create_reservation_guest_name_round_trips_when_long XFAIL [ 93%]
tests/test_certification.py::test_update_unknown_reservation_returns_404 XFAIL [100%]

======================== 12 passed, 3 xfailed in 0.85s =========================
```

The three quirk tests are marked `xfail(strict=True)`: pytest treats an
*expected* failure as a pass (exit code 0), but if a quirk is ever fixed in
the mock and the test starts unexpectedly *passing*, `strict=True` turns
that into a reported failure (XPASS) — a deliberate tripwire so a fixed
defect doesn't go unnoticed.

## Repo layout

```
mock_crs/                  FastAPI mock CRS (the system under test)
spec/crs-openapi.yaml      OpenAPI 3.0 contract
collection/                Postman v2.1 collection + local environment
scripts/
  run_certification.py     Newman-path runner (start API, run Newman, stop, sign off)
  run_python_tests.py      Pure-Python fallback runner (pytest)
  generate_signoff.py      Newman JSON -> reports/certification-report.md (+ html)
  _build_collection.py     One-off generator used to author the Postman collection
tests/                     pytest suite mirroring the Postman assertions
reports/                   Committed real report from an actual run
run_certification.sh       Thin CLI wrapper around scripts/run_certification.py
.github/workflows/ci.yml   Runs the Python fallback path on every push/PR
```

## What's honestly left out (roadmap)

This is a portfolio-scale project, not a production certification platform.
Known gaps, in rough priority order if this were to grow:

- **No persistence.** The mock API is in-memory only; a real harness would
  need a way to certify against a partner's *actual* staging environment,
  which means configurable base URLs, auth (API keys/OAuth), and retries for
  flaky networks — none of which is needed for an offline demo but all of
  which is needed for a real partner.
- **No schema-diff against the OpenAPI spec at request time.** The spec and
  the Postman assertions are both hand-authored to match; a more rigorous
  harness would validate every response against the OpenAPI schema
  automatically (e.g. via `schemathesis` or `dredd`) instead of relying on
  hand-written `pm.test` field checks.
- **Single-iteration, single-tenant.** No property-level or currency-level
  fuzzing, no concurrency/race-condition tests (e.g. double-booking the same
  room), no rate-limiting behavior.
- **No historical trend tracking.** Each run produces one report; there's no
  storage of past runs to show a partner's pass rate improving over time.
- **HTML report styling is minimal.** `newman-reporter-htmlextra` produces a
  much richer HTML report than `certification-report.html` — both are
  generated and both are included, but they're not unified into one
  branded artifact.
- **No auth/security test category.** A real certification cycle usually
  also checks things like rate limiting, auth token expiry, and injection
  resistance; this harness is scoped to functional/business-rule
  correctness only.

## Requirements

- Python 3.10+
- Node.js + npm (only for the Newman path — `npm install newman
  newman-reporter-htmlextra`; the Python fallback path needs neither)

## License

MIT — see [LICENSE](LICENSE).

## Author

Pakshal Shah — [pakshalshah.com](https://pakshalshah.com)
