import asyncio
import base64
import json
import websockets
import os
from dotenv import load_dotenv
from agent_functions import FUNCTION_DEFINITIONS, FUNCTION_MAP, get_office_status
from prompt import build_receptionist_prompt

load_dotenv()

def sts_connect():
    api_key = os.getenv('DG_API_KEY')
    if not api_key:
        raise ValueError("DG_API_KEY environment variable is not set")

    sts_ws = websockets.connect(
        "wss://agent.deepgram.com/v1/agent/converse",
        subprotocols=["token", api_key]
    )
    return sts_ws

async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()

    async with sts_connect() as sts_ws:
        within_hours, current_time = get_office_status()
        office_status = "OPEN" if within_hours else "CLOSED"
        system_prompt = f"""
        You are **Brookline Progressive Dental's virtual receptionist**, a friendly, professional
        AI voice agent who speaks naturally with callers. You represent the Brookline Progressive Dental team
        and should always sound warm, confident, and genuinely caring — like a real person who loves helping patients.

        Current time: {current_time} ({office_status})

        ---

        ## Your Role & Objectives
        You are the first point of contact for all callers. Your job is to:
        1. Greet the caller warmly and find out how you can help.
        2. Identify if the caller is a **new** or **existing** patient (only after understanding their need).
        3. Determine if the **office is open** using `check_office_hours()`.
        4. Gather accurate contact information and reason for the call.
        5. Handle the conversation naturally and close it gracefully.

        ---

        ## The Brookline Call Flow

        1. **Greeting**
        - Start warmly, like a real receptionist:
            “Hello, thank you for calling Brookline Progressive Dental! How can I help you today?”
        - Let the caller explain first. Don't interrupt.
        - Once you understand their request, gently clarify:
            “Just so I can best assist — are you a new patient or have you visited us before?”
        - Record this as `patientType`: `"new"`, `"existing"`, or `"other"`.

        2. **If the caller is new**
        - Express enthusiasm:
            “That's wonderful! We love welcoming new patients to Brookline Progressive Dental.”
        - Proceed to collect their contact details.

        Before asking the caller for contact information, call check_office_hours() **only once**. Do not repeat the function call, even if the caller speaks while checking. Use the result to decide next steps.

        3. **Check Office Hours**
        - Use `check_office_hours()` to confirm whether the office is open.
        - If **closed**:
            - Kindly explain the office is currently closed.
            - Collect **full name**, **phone number**, **email**, and **reason for call**.
            - Always ask for the *full name in one go*, e.g.:
                “Can I have your full name please?”
            - The backend will automatically split it into first and last name.
            - If the caller only provides one name, record it as first name and set last name to “Unknown.”
            - If the caller declines to provide something, record it as “Unknown.”
            - Save the data with `capture_contact()` and end the call via `end_call(farewell_type="bye")`.

        - If **open**:
            - Confirm and validate **email** and **phone** with `validate_contact()`.
            - If validation fails, ask naturally for a repeat:
                “I might have misheard your email — could you repeat that for me?”
            - Save details with `capture_contact()`.
            - Then say:
                “Thank you! I'll forward your call to our Brookline team now.”
            - End with `end_call(farewell_type="general")`.

        4. **Call Ending**
        - If the caller says “bye”, “thank you”, or “have a nice day,” gracefully end using `end_call()`.

        ---

        ## Data & Function Rules
        - Always call `capture_contact()` before ending the call.
        - `patientType` must be `"new"` or `"existing"` (use `"other"` only if unsure).
        - Store the reason for call as `message`.
        - Always include: `preferredDays`, `preferredTimes`, `servicesInterested`, and `source` in the final record.

        ---

        ## Behavioral Guidelines
        - Sound **human**, warm, and conversational.
        - Avoid robotic phrasing — speak like a friendly receptionist.
        - If user's answer is unclear, **ask again politely** to clarify.
        - Use the caller's name naturally once known.
        - Never interrupt; let them finish before you respond.
        - Use small fillers or affirmations (“Sure,” “Got it,” “No problem!”) when appropriate.
        - Stay calm, polite, and empathetic even if the caller is uncertain or rushed.
        - If unsure what to do, prioritize **sounding real and helpful** over following exact wording.

        If anything is unclear — act as a real dental receptionist would.
        """

        config_message = {
            "type": "Settings",
            "audio": {
                "input": {"encoding": "mulaw", "sample_rate": 8000},
                "output": {"encoding": "mulaw", "sample_rate": 8000, "container": "none"},
            },
            "agent": {
                "language": "en",
                "listen": {"provider": {"type": "deepgram", "model": "nova-3"}},
                "think": {
                    "provider": {
                        "type": "open_ai",
                        "model": "gpt-4o",
                        "temperature": 0.7,
                    },
                    "prompt": system_prompt,
                    "functions": [
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
                                    "fullName": {
                                        "type": "string",
                                        "description": "The caller's full name"
                                    },
                                    "email": {"type": "string"},
                                    "phoneNumber": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                                "required": ["fullName", "phoneNumber"],
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
                                "required": []
                            },   
                        }
                    ]
                },
                "speak": {"provider": {"type": "deepgram", "model": "aura-2-thalia-en"}},
                "greeting": "Hi there! Thanks for calling Brookline Progressive Dental. How can I help you today?"
            },
        }

        await sts_ws.send(json.dumps(config_message))

        async def sts_sender(sts_ws):
            print("sts_sender started")
            while True:
                chunk = await audio_queue.get()
                await sts_ws.send(chunk)

        async def sts_receiver(sts_ws):
            print("sts_receiver started")
            streamsid = await streamsid_queue.get()

            async for message in sts_ws:
                try:
                    # Text messages from Deepgram (JSON)
                    if isinstance(message, str):
                        decoded = json.loads(message)

                        msg_type = decoded.get("type")

                        # ✅ Confirm settings applied before streaming audio
                        if msg_type == "SettingsApplied":
                            print("✅ Deepgram settings applied. Ready to process audio.")
                            continue

                        # ✅ Handle function calls from Deepgram
                        if msg_type == "FunctionCall":
                            fn_name = decoded.get("name")
                            fn_id = decoded.get("id")
                            params = decoded.get("parameters", {})

                            print(f"\n⚙️ FunctionCall → {fn_name}")
                            print(f"→ Params: {json.dumps(params, indent=2)}")

                            if fn_name in FUNCTION_MAP:
                                fn = FUNCTION_MAP[fn_name]
                                try:
                                    # Run async or sync function safely
                                    if asyncio.iscoroutinefunction(fn):
                                        result = await fn(params)
                                    else:
                                        result = fn(params)

                                    response = {
                                        "type": "FunctionCallResponse",
                                        "name": fn_name,
                                        "result": result,
                                    }
                                    if fn_id:
                                        response["id"] = fn_id  # echo back id if present

                                    await sts_ws.send(json.dumps(response))
                                    print(f"← Result: {json.dumps(result, indent=2)}")

                                except Exception as e:
                                    error_response = {
                                        "type": "FunctionCallResponse",
                                        "name": fn_name,
                                        "error": str(e),
                                    }
                                    if fn_id:
                                        error_response["id"] = fn_id
                                    await sts_ws.send(json.dumps(error_response))
                                    print(f"❌ Function {fn_name} failed: {e}")

                            else:
                                print(f"⚠️ Unknown function call: {fn_name}")

                            continue

                        # ✅ Handle barge-in (user starts speaking mid-TTS)
                        if msg_type == "UserStartedSpeaking":
                            print("🎤 User started speaking — clearing Twilio buffer.")
                            clear_message = {"event": "clear", "streamSid": streamsid}
                            await twilio_ws.send(json.dumps(clear_message))
                            continue

                        # Other message types (debug/info logs)
                        if msg_type not in ("SettingsApplied", "FunctionCall", "UserStartedSpeaking"):
                            print(f"🪶 STS Message: {decoded}")

                            # ⚠️ Detect "think provider" internal errors
                            if (
                                "code" in decoded
                                and decoded.get("code") == "THINK_REQUEST_FAILED"
                            ):
                                print("⚠️ Think provider failed — retrying...")
                                await sts_ws.send(json.dumps({
                                    "type": "Response",
                                    "content": "I'm sorry, could you repeat that one more time?"
                                }))
                                continue

                            if decoded.get("code") == "UNPARSABLE_CLIENT_MESSAGE":
                                # Politely ask for clarification
                                await sts_ws.send(json.dumps({
                                    "type": "Response",
                                    "content": "I'm sorry, I didn't catch that"
                                }))
                                continue



                    # Binary audio from Deepgram (TTS output)
                    else:
                        raw_mulaw = message
                        media_message = {
                            "event": "media",
                            "streamSid": streamsid,
                            "media": {
                                "payload": base64.b64encode(raw_mulaw).decode("ascii")
                            },
                        }
                        await twilio_ws.send(json.dumps(media_message))

                except Exception as e:
                    print(f"❌ Error in sts_receiver loop: {e}")

        async def twilio_receiver(twilio_ws):
            print("twilio_receiver started")
            BUFFER_SIZE = 20 * 160

            inbuffer = bytearray(b"")
            async for message in twilio_ws:
                try:
                    data = json.loads(message)
                    if data["event"] == "start":
                        print("got our streamsid")
                        start = data["start"]
                        streamsid = start["streamSid"]
                        streamsid_queue.put_nowait(streamsid)
                    if data["event"] == "connected":
                        continue
                    if data["event"] == "media":
                        media = data["media"]
                        chunk = base64.b64decode(media["payload"])
                        if media["track"] == "inbound":
                            inbuffer.extend(chunk)
                    if data["event"] == "stop":
                        break

                    while len(inbuffer) >= BUFFER_SIZE:
                        chunk = inbuffer[:BUFFER_SIZE]
                        audio_queue.put_nowait(chunk)
                        inbuffer = inbuffer[BUFFER_SIZE:]
                except:
                    break

        await asyncio.wait(
            [
                asyncio.ensure_future(sts_sender(sts_ws)),
                asyncio.ensure_future(sts_receiver(sts_ws)),
                asyncio.ensure_future(twilio_receiver(twilio_ws)),
            ]
        )

        await twilio_ws.close()


async def router(websocket):
    print("Incoming connection")
    await twilio_handler(websocket)

async def main():
    server = await websockets.serve(router, "0.0.0.0", 5000)
    print("✅ Server started on wss://voice.tasloflow.com")

    # Run forever
    await asyncio.Future()  # keeps the server alive


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Server stopped.")
