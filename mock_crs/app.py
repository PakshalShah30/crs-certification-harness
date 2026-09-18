"""
Mock CRS (Central Reservation System) API.

This is a deliberately imperfect implementation of a hotel-industry style
reservation API. It exists so the certification harness in this repo has
something realistic to certify against, offline, with no external services.

It implements a realistic subset of a CRS surface:
    GET    /health
    GET    /properties
    GET    /rates
    GET    /availability
    POST   /reservations
    GET    /reservations/{id}
    PUT    /reservations/{id}
    DELETE /reservations/{id}

Deliberate quirks (see README.md "Deliberate API quirks" section for the
full write-up of why these are realistic and which certification test
catches each one):

  QUIRK 1 - No date-order validation on create.
      POST /reservations happily accepts a checkout date that is before
      (or equal to) the checkin date. Real CRS integrations have shipped
      this bug; it silently corrupts availability math downstream.

  QUIRK 2 - Wrong status code on update-not-found.
      PUT /reservations/{id} returns HTTP 200 with an `{"error": ...}`
      body when the reservation does not exist, instead of HTTP 404.
      This is one of the single most common certification failures in
      real partner integrations: "200 OK" bodies that are actually errors.

  QUIRK 3 - Silent truncation of guest_name.
      Guest names longer than 50 characters are silently truncated
      instead of being rejected with a 4xx validation error. The caller
      gets back a "successful" reservation with corrupted guest data.

Everything is in-memory. Restarting the process resets all state.
"""
from __future__ import annotations

import itertools
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

app = FastAPI(
    title="Mock CRS API",
    description="Offline mock Central Reservation System API used to exercise the "
    "CRS Certification Harness.",
    version="1.0.0",
)

GUEST_NAME_MAX_LEN = 50

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

PROPERTIES = [
    {
        "property_id": "HTL-001",
        "name": "Harborview Grand Hotel",
        "city": "Boston",
        "country": "US",
        "star_rating": 4,
    },
    {
        "property_id": "HTL-002",
        "name": "Desert Palms Resort",
        "city": "Scottsdale",
        "country": "US",
        "star_rating": 5,
    },
    {
        "property_id": "HTL-003",
        "name": "Old Town Inn",
        "city": "Prague",
        "country": "CZ",
        "star_rating": 3,
    },
]
PROPERTY_IDS = {p["property_id"] for p in PROPERTIES}

RATES = [
    {
        "rate_id": "RATE-STD-001",
        "property_id": "HTL-001",
        "room_type": "standard_queen",
        "nightly_rate": 189.00,
        "currency": "USD",
        "refundable": True,
    },
    {
        "rate_id": "RATE-DLX-001",
        "property_id": "HTL-001",
        "room_type": "deluxe_king",
        "nightly_rate": 249.00,
        "currency": "USD",
        "refundable": True,
    },
    {
        "rate_id": "RATE-STD-002",
        "property_id": "HTL-002",
        "room_type": "standard_queen",
        "nightly_rate": 329.00,
        "currency": "USD",
        "refundable": False,
    },
    {
        "rate_id": "RATE-STE-002",
        "property_id": "HTL-002",
        "room_type": "suite",
        "nightly_rate": 599.00,
        "currency": "USD",
        "refundable": True,
    },
    {
        "rate_id": "RATE-STD-003",
        "property_id": "HTL-003",
        "room_type": "standard_double",
        "nightly_rate": 92.00,
        "currency": "EUR",
        "refundable": True,
    },
]

# Rooms held back as "sold out" for a couple of dates, purely so
# /availability has something interesting to report.
SOLD_OUT_DATES = {"HTL-002": {"2026-12-31", "2027-01-01"}}

_reservation_id_seq = itertools.count(1001)
RESERVATIONS: dict[str, dict] = {}


def _new_reservation_id() -> str:
    return f"RES-{next(_reservation_id_seq)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReservationCreate(BaseModel):
    property_id: str
    rate_id: str
    room_type: str
    checkin: date
    checkout: date
    guest_name: str = Field(..., min_length=1)
    guest_email: EmailStr
    guests: int = Field(default=1, ge=1, le=10)

    # NOTE: deliberately does NOT validate checkout > checkin.
    # See QUIRK 1 in the module docstring.


class ReservationUpdate(BaseModel):
    checkin: Optional[date] = None
    checkout: Optional[date] = None
    guest_name: Optional[str] = None
    guest_email: Optional[EmailStr] = None
    guests: Optional[int] = Field(default=None, ge=1, le=10)
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok", "time": _now_iso()}


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@app.get("/properties")
def list_properties():
    return {"properties": PROPERTIES, "count": len(PROPERTIES)}


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------


@app.get("/rates")
def list_rates(property_id: Optional[str] = Query(default=None)):
    if property_id is not None:
        if property_id not in PROPERTY_IDS:
            raise HTTPException(status_code=404, detail=f"Unknown property_id '{property_id}'")
        rates = [r for r in RATES if r["property_id"] == property_id]
    else:
        rates = RATES
    return {"rates": rates, "count": len(rates)}


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


@app.get("/availability")
def get_availability(
    property_id: str = Query(...),
    checkin: date = Query(...),
    checkout: date = Query(...),
):
    if property_id not in PROPERTY_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown property_id '{property_id}'")

    if checkout <= checkin:
        raise HTTPException(
            status_code=422,
            detail="checkout must be strictly after checkin",
        )

    blocked = SOLD_OUT_DATES.get(property_id, set())
    requested_dates = {
        d.isoformat()
        for d in _date_range(checkin, checkout)
    }
    is_sold_out = bool(requested_dates & blocked)

    property_rates = [r for r in RATES if r["property_id"] == property_id]
    rooms = []
    if not is_sold_out:
        for r in property_rates:
            rooms.append(
                {
                    "room_type": r["room_type"],
                    "rate_id": r["rate_id"],
                    "nightly_rate": r["nightly_rate"],
                    "currency": r["currency"],
                }
            )

    return {
        "property_id": property_id,
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "available": not is_sold_out and len(rooms) > 0,
        "rooms": rooms,
    }


def _date_range(start: date, end: date):
    cur = start
    while cur < end:
        yield cur
        cur = date.fromordinal(cur.toordinal() + 1)


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------


@app.post("/reservations", status_code=status.HTTP_201_CREATED)
def create_reservation(payload: ReservationCreate):
    if payload.property_id not in PROPERTY_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown property_id '{payload.property_id}'")

    rate = next(
        (r for r in RATES if r["rate_id"] == payload.rate_id and r["property_id"] == payload.property_id),
        None,
    )
    if rate is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown rate_id '{payload.rate_id}' for property '{payload.property_id}'",
        )

    # --- QUIRK 1 -----------------------------------------------------
    # There is intentionally NO check here that payload.checkout is
    # after payload.checkin. A conforming CRS must reject
    # checkout <= checkin with a 4xx. This mock does not, and the
    # certification collection's "checkout after checkin" test is
    # designed to catch exactly this.
    # -------------------------------------------------------------

    # --- QUIRK 3 -----------------------------------------------------
    # Guest names over GUEST_NAME_MAX_LEN characters are silently
    # truncated rather than rejected. The client believes the booking
    # was created faithfully; the stored/returned guest_name is
    # actually shorter than what was submitted.
    # -------------------------------------------------------------
    stored_guest_name = payload.guest_name[:GUEST_NAME_MAX_LEN]

    reservation_id = _new_reservation_id()
    nights = max((payload.checkout - payload.checkin).days, 0)
    total_amount = round(rate["nightly_rate"] * nights, 2)

    reservation = {
        "reservation_id": reservation_id,
        "property_id": payload.property_id,
        "rate_id": payload.rate_id,
        "room_type": payload.room_type,
        "checkin": payload.checkin.isoformat(),
        "checkout": payload.checkout.isoformat(),
        "guest_name": stored_guest_name,
        "guest_email": payload.guest_email,
        "guests": payload.guests,
        "nights": nights,
        "nightly_rate": rate["nightly_rate"],
        "currency": rate["currency"],
        "total_amount": total_amount,
        "status": "confirmed",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    RESERVATIONS[reservation_id] = reservation
    return reservation


@app.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: str):
    reservation = RESERVATIONS.get(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail=f"Reservation '{reservation_id}' not found")
    return reservation


@app.put("/reservations/{reservation_id}")
def update_reservation(reservation_id: str, payload: ReservationUpdate):
    reservation = RESERVATIONS.get(reservation_id)

    # --- QUIRK 2 -----------------------------------------------------
    # A conforming API must return 404 for an update to a reservation
    # that does not exist. This mock instead returns HTTP 200 with an
    # error payload, which is a classic real-world CRS certification
    # failure: callers who only check `resp.ok` / status code 2xx
    # never notice the update silently failed.
    # -------------------------------------------------------------
    if reservation is None:
        return JSONResponse(
            status_code=200,
            content={"error": f"Reservation '{reservation_id}' not found", "reservation_id": reservation_id},
        )

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "guest_name" and value is not None:
            value = value[:GUEST_NAME_MAX_LEN]
        if isinstance(value, date):
            value = value.isoformat()
        reservation[field] = value

    reservation["updated_at"] = _now_iso()
    return reservation


@app.delete("/reservations/{reservation_id}")
def cancel_reservation(reservation_id: str):
    reservation = RESERVATIONS.get(reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail=f"Reservation '{reservation_id}' not found")
    reservation["status"] = "cancelled"
    reservation["updated_at"] = _now_iso()
    return {"reservation_id": reservation_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Convenience: reset endpoint for test isolation (not part of the "real"
# CRS contract - not documented in the OpenAPI spec - used only so the
# certification suite can run repeatedly against a clean slate without
# restarting the process).
# ---------------------------------------------------------------------------


@app.post("/_test/reset", include_in_schema=False)
def _reset_state():
    RESERVATIONS.clear()
    global _reservation_id_seq
    _reservation_id_seq = itertools.count(1001)
    return {"reset": True}
