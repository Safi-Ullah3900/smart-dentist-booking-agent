"""
Biz Booking Agent — MCP Server
================================
Domain: Bright Smiles Dental Clinic

Exposes 5 tools via stdio transport using FastMCP:
  1. check_availability    → Check open appointment slots
  2. create_booking        → Register a new booking in the system
  3. get_booking_details   → Retrieve a booking by ID
  4. cancel_booking        → Cancel an existing booking
  5. get_service_catalog   → List all services with prices & durations

Used by:
  - booking_agent  (create_booking, check_availability, get_booking_details)
  - faq_agent      (get_service_catalog, check_availability)
"""

import json
import random
import string
from datetime import datetime, timedelta
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# In-memory "database" (resets each server restart — demo only)
# ---------------------------------------------------------------------------
_bookings: dict[str, dict] = {}  # booking_id → booking_record

# Clinic schedule: slots per day
_SLOT_TIMES = [
    "09:00", "09:45", "10:30", "11:15",
    "12:00", "12:45", "14:00", "14:45",
    "15:30", "16:15", "17:00", "17:45",
    "18:30",
]

# Services catalog
_SERVICE_CATALOG = {
    "general_checkup": {
        "name": "General Dental Checkup",
        "price_pkr": 1500,
        "duration_min": 45,
        "description": "Comprehensive oral examination including X-rays if needed.",
    },
    "teeth_cleaning": {
        "name": "Professional Teeth Cleaning (Scaling)",
        "price_pkr": 2500,
        "duration_min": 60,
        "description": "Removes plaque and tartar buildup using ultrasonic instruments.",
    },
    "tooth_extraction": {
        "name": "Tooth Extraction",
        "price_pkr": 4500,
        "duration_min": 45,
        "description": "Simple or surgical extraction under local anaesthesia. Price may vary.",
    },
    "root_canal": {
        "name": "Root Canal Treatment (RCT)",
        "price_pkr": 15000,
        "duration_min": 90,
        "description": "Multi-session RCT to save severely decayed or infected teeth.",
    },
    "teeth_whitening": {
        "name": "Teeth Whitening (In-Office)",
        "price_pkr": 8000,
        "duration_min": 75,
        "description": "Professional-grade whitening using hydrogen peroxide gel.",
    },
    "braces_consultation": {
        "name": "Braces / Orthodontic Consultation",
        "price_pkr": 0,
        "duration_min": 30,
        "description": "Free first consultation with our orthodontist Dr. Umar Farooq.",
    },
    "dental_filling": {
        "name": "Dental Filling (Composite / Amalgam)",
        "price_pkr": 3000,
        "duration_min": 40,
        "description": "Tooth-coloured composite or silver amalgam fillings.",
    },
    "dentures": {
        "name": "Dentures (Partial / Full)",
        "price_pkr": 25000,
        "duration_min": 60,
        "description": "Custom-made removable dentures. Full set or partial plate.",
    },
}

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _generate_booking_id() -> str:
    """Generate a unique booking ID like BK-A3F9."""
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"BK-{suffix}"


def _get_booked_slots(date: str) -> list[str]:
    """Return list of already-booked time slots for a given date."""
    return [
        b["time_slot"]
        for b in _bookings.values()
        if b["date"] == date and b["status"] == "confirmed"
    ]


def _validate_date(date_str: str) -> Optional[str]:
    """Parse a date string, return ISO date or None if invalid."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# FastMCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "biz-booking-mcp",
    instructions=(
        "MCP server for Bright Smiles Dental Clinic. "
        "Provides tools to check appointment availability, manage bookings, "
        "and retrieve the service catalog."
    ),
)


@mcp.tool()
def check_availability(
    date: str,
    service_id: Optional[str] = None,
) -> str:
    """Check available appointment slots for a given date.

    Args:
        date: The date to check in YYYY-MM-DD format (e.g., '2026-07-10').
        service_id: Optional service ID to filter slot duration. If omitted, all slots shown.

    Returns:
        JSON string with available time slots for that date.
    """
    iso_date = _validate_date(date)
    if not iso_date:
        return json.dumps({"error": f"Invalid date format: '{date}'. Use YYYY-MM-DD."})

    # Block Sundays (clinic opens 10-14 only on Sunday for emergencies)
    parsed = datetime.strptime(iso_date, "%Y-%m-%d")
    is_sunday = parsed.weekday() == 6

    available_slots = _SLOT_TIMES[:]
    if is_sunday:
        available_slots = ["10:00", "11:00", "12:00", "13:00"]

    booked = _get_booked_slots(iso_date)
    free_slots = [s for s in available_slots if s not in booked]

    service_info = None
    if service_id and service_id in _SERVICE_CATALOG:
        service_info = {
            "service": _SERVICE_CATALOG[service_id]["name"],
            "duration_min": _SERVICE_CATALOG[service_id]["duration_min"],
        }

    return json.dumps({
        "date": iso_date,
        "day_of_week": parsed.strftime("%A"),
        "is_sunday": is_sunday,
        "available_slots": free_slots,
        "booked_count": len(booked),
        "service_info": service_info,
    }, ensure_ascii=False)


@mcp.tool()
def create_booking(
    patient_name: str,
    contact: str,
    service_id: str,
    date: str,
    time_slot: str,
    doctor_preference: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """Create a new appointment booking for a patient.

    Args:
        patient_name: Full name of the patient.
        contact: Patient's mobile number (Pakistani format preferred).
        service_id: Service key from the service catalog (e.g., 'general_checkup').
        date: Appointment date in YYYY-MM-DD format.
        time_slot: Appointment time slot (e.g., '10:30').
        doctor_preference: Optional doctor preference ('Dr. Ayesha Khan' or 'Dr. Umar Farooq').
        notes: Optional notes or special requests.

    Returns:
        JSON with booking confirmation details or error message.
    """
    # Validate date
    iso_date = _validate_date(date)
    if not iso_date:
        return json.dumps({"error": f"Invalid date: '{date}'."})

    # Validate service
    if service_id not in _SERVICE_CATALOG:
        valid = list(_SERVICE_CATALOG.keys())
        return json.dumps({"error": f"Unknown service '{service_id}'. Valid IDs: {valid}"})

    # Check time slot availability
    booked_slots = _get_booked_slots(iso_date)
    if time_slot in booked_slots:
        return json.dumps({
            "error": f"Time slot {time_slot} on {iso_date} is already booked.",
            "hint": "Call check_availability to find a free slot.",
        })

    # Create record
    booking_id = _generate_booking_id()
    service = _SERVICE_CATALOG[service_id]
    record = {
        "booking_id": booking_id,
        "patient_name": patient_name,
        "contact": contact,
        "service_id": service_id,
        "service_name": service["name"],
        "price_pkr": service["price_pkr"],
        "duration_min": service["duration_min"],
        "date": iso_date,
        "time_slot": time_slot,
        "doctor_preference": doctor_preference or "No preference",
        "notes": notes or "",
        "status": "confirmed",
        "created_at": datetime.now().isoformat(),
    }
    _bookings[booking_id] = record

    return json.dumps({
        "success": True,
        "booking_id": booking_id,
        "message": (
            f"Appointment confirmed for {patient_name} on {iso_date} at {time_slot}. "
            f"Service: {service['name']} (Rs. {service['price_pkr']:,}). "
            f"Please arrive 10 minutes early."
        ),
        "booking": record,
    }, ensure_ascii=False)


@mcp.tool()
def get_booking_details(booking_id: str) -> str:
    """Retrieve full details of an existing booking by its ID.

    Args:
        booking_id: The booking reference ID (e.g., 'BK-A3F9').

    Returns:
        JSON with booking details or an error if not found.
    """
    booking_id = booking_id.upper().strip()
    if booking_id not in _bookings:
        return json.dumps({
            "error": f"Booking '{booking_id}' not found.",
            "hint": "Check the booking ID and try again.",
        })
    return json.dumps(_bookings[booking_id], ensure_ascii=False)


@mcp.tool()
def cancel_booking(booking_id: str, reason: Optional[str] = None) -> str:
    """Cancel an existing appointment booking.

    Args:
        booking_id: The booking reference ID to cancel (e.g., 'BK-A3F9').
        reason: Optional cancellation reason for the clinic's records.

    Returns:
        JSON confirmation of cancellation or error message.
    """
    booking_id = booking_id.upper().strip()
    if booking_id not in _bookings:
        return json.dumps({"error": f"Booking '{booking_id}' not found."})

    booking = _bookings[booking_id]
    if booking["status"] == "cancelled":
        return json.dumps({"error": f"Booking '{booking_id}' is already cancelled."})

    booking["status"] = "cancelled"
    booking["cancelled_at"] = datetime.now().isoformat()
    booking["cancel_reason"] = reason or "No reason provided"

    return json.dumps({
        "success": True,
        "booking_id": booking_id,
        "message": (
            f"Booking {booking_id} for {booking['patient_name']} on "
            f"{booking['date']} at {booking['time_slot']} has been cancelled."
        ),
        "refund_note": "No cancellation fee if cancelled ≥2 hours before appointment.",
    }, ensure_ascii=False)


@mcp.tool()
def get_service_catalog(service_id: Optional[str] = None) -> str:
    """Retrieve the clinic's service catalog with prices and durations.

    Args:
        service_id: Optional specific service ID to retrieve. If omitted, returns all services.

    Returns:
        JSON with service details (name, price in PKR, duration in minutes, description).
    """
    if service_id:
        if service_id not in _SERVICE_CATALOG:
            return json.dumps({
                "error": f"Service '{service_id}' not found.",
                "valid_ids": list(_SERVICE_CATALOG.keys()),
            })
        return json.dumps({service_id: _SERVICE_CATALOG[service_id]}, ensure_ascii=False)

    return json.dumps({
        "clinic": "Bright Smiles Dental Clinic",
        "currency": "PKR",
        "services": _SERVICE_CATALOG,
        "note": "Prices may vary based on case complexity. Call +92-42-3456-7890 for exact quotes.",
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Entrypoint (run as stdio MCP server)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
