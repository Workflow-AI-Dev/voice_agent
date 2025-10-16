from agent_functions import FUNCTION_MAP
import asyncio

async def test():
    print(await FUNCTION_MAP["check_office_hours"]({}))
    print(await FUNCTION_MAP["validate_contact"]({"email": "testexample.com", "phone": "+12345678901"}))
    print(await FUNCTION_MAP["capture_contact"]({
        "patientType": "new",
        "fullName": "John Doe",
        "email": "john@example.com",
        "phoneNumber": "1234567890",
        "reason": "teeth cleaning"
    }))
    print(await FUNCTION_MAP["end_call"]({"farewell_type": "bye"}))

asyncio.run(test())
