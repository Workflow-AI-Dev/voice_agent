import asyncio
from datetime import datetime
import pytz
import json
import os
import re

# ------------------------------------------------------------------
# Office hours configuration
# ------------------------------------------------------------------
OFFICE_HOURS = {
    "Monday": (8, 16),
    "Tuesday": (8, 17),
    "Wednesday": (8, 19),
    "Thursday": (8, 17),
    "Friday": (8, 15),
    "Saturday": (8, 14),
    "Sunday": None,
}

def get_office_status():
    tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)
    day = now.strftime("%A")
    hours = OFFICE_HOURS.get(day)
    if not hours:
        return False, f"{day} {now.strftime('%I:%M %p')}"
    start, end = hours
    within_hours = start <= now.hour < end
    return within_hours, f"{day} {now.strftime('%I:%M %p')}"

# ------------------------------------------------------------------
# Helper validation functions
# ------------------------------------------------------------------
def validate_email(email: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", email))

def validate_phone(phone: str) -> bool:
    return bool(re.match(r"^\+?1?\d{10,15}$", phone))

# ------------------------------------------------------------------
# JSON persistence
# ------------------------------------------------------------------
def save_call_data(data: dict):
    os.makedirs("data/calls", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"data/calls/{timestamp}.json"
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Saved call data → {filename}")
    return {"status": "saved", "file": filename}

# ------------------------------------------------------------------
# Async functions exposed to Deepgram's think() layer
# ------------------------------------------------------------------

async def check_office_hours(params):
    """Return whether the office is open right now."""
    within_hours, current_time = get_office_status()
    return {"is_open": within_hours, "time": current_time}

async def validate_contact(params):
    """Validate email and phone and return flags."""
    email = params.get("email")
    phone = params.get("phone")
    valid_email = validate_email(email) if email else False
    valid_phone = validate_phone(phone) if phone else False
    return {"valid_email": valid_email, "valid_phone": valid_phone}

async def capture_contact(params):
    """Capture caller contact info and save as JSON"""
    full_name = params.get("fullName", "").strip()
    email = params.get("email", "Unknown")
    phone = params.get("phoneNumber", "Unknown")
    reason = params.get("reason", "Unknown")
    patient_type = params.get("patientType", "other")

    first_name, last_name = "Unknown", "Unknown"
    if full_name:
        parts = full_name.split()
        if len(parts) == 1:
            first_name = parts[0]
        else:
            first_name = parts[0]
            last_name = " ".join(parts[1:])

    record = {
        "patientType": patient_type,
        "fullName": full_name,
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "phoneNumber": phone,
        "preferredDays": ["Mon", "Wed"],
        "preferredTimes": ["AM", "PM"],
        "servicesInterested": None,
        "message": reason,
        "source": ["voice-agent"],
    }

    return save_call_data(record)


async def end_call(params):
    """Gracefully close the conversation."""
    farewell_type = params.get("farewell_type", "general")
    message = "Thank you for calling Brookline Progressive Dental. Have a wonderful day!"
    if farewell_type == "thanks":
        message = "You're very welcome! Have a great day!"
    elif farewell_type == "bye":
        message = "Goodbye! Thank you for calling Brookline Progressive Dental!"
    return {"message": message}

# ------------------------------------------------------------------
# Function schema definitions for Deepgram
# ------------------------------------------------------------------
FUNCTION_DEFINITIONS = [
    {
        "name": "check_office_hours",
        "description": "Check whether Brookline Progressive Dental is currently open.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "validate_contact",
        "description": "Validate provided email and phone number formats.",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "phone": {"type": "string"},
            },
            "required": []
        },
    },
    {
        "name": "capture_contact",
        "description": "Save caller contact details and reason for call into a JSON file.",
        "parameters": {
            "type": "object",
            "properties": {
                "patientType": {
                    "type": "string",
                    "description": "Whether the caller is a new or existing patient.",
                    "enum": ["new", "existing", "other"]
                },
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "email": {"type": "string"},
                "phoneNumber": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["phoneNumber"],
        },
    },
    {
        "name": "end_call",
        "description": "End the call and say goodbye.",
        "parameters": {
            "type": "object",
            "properties": {
                "farewell_type": {
                    "type": "string",
                    "enum": ["thanks", "general", "bye"],
                }
            },
        },
        "required": []
    },
]

FUNCTION_MAP = {
    "check_office_hours": check_office_hours,
    "validate_contact": validate_contact,
    "capture_contact": capture_contact,
    "end_call": end_call,
}
