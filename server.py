import asyncio
import base64
import json
import websockets
import os
from dotenv import load_dotenv
from datetime import datetime
import pytz
from agent_functions import get_office_status

load_dotenv()

def sts_connect():
    api_key = os.getenv('DG_API_KEY')
    if not api_key:
        raise ValueError("DG_API_KEY environment variable is not set")

    return websockets.connect(
        "wss://agent.deepgram.com/v1/agent/converse",
        subprotocols=["token", api_key]
    )


async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()
    dialog_history = []

    async with sts_connect() as sts_ws:
        # --- get current date and office status ---
        tz = pytz.timezone("America/New_York")
        now = datetime.now(tz)
        current_date = now.strftime("%A, %B %d, %Y %I:%M %p")
        within_hours, current_time = get_office_status()
        office_status = "OPEN" if within_hours else "CLOSED"

        # --- build prompt with dynamic date and office status ---
        system_prompt = f"""
        CURRENT DATE AND TIME CONTEXT:
        - Today is {current_date}. Use this as context when discussing appointments and office hours.
        - The office is currently {office_status} (as of {current_time}).
        - When referencing dates to callers, use relative terms like "tomorrow", "next Tuesday", or "this Friday" if within 7 days.

        PERSONALITY & TONE:
        - Warm, professional, and friendly — like a real dental receptionist.
        - Speak naturally and conversationally; avoid robotic listing or instructions.
        - Show empathy, patience, and enthusiasm for new patients.
        - Use natural affirmations like "Sure", "Got it", "No problem!" when appropriate.

        CALL FLOW & LOGIC:
        1. Greeting: "Hello, thank you for calling Brookline Progressive Dental! How can I help you today?"
        - Let caller speak first.
        - Clarify politely if request is unclear.

        2. Identify Patient Type:
        - Ask: "Just so I can best assist, are you a new patient or have you visited us before?"
        - Record as patientType: "new", "existing", or "other".

        3. For New Patients:
        - Express enthusiasm.
        - Collect full name, phone, email, and reason.
        - Validate email and phone internally; if invalid, ask naturally to repeat.

        4. Office Hours Handling:
        - Office is currently {office_status}.
        - If CLOSED: "Our office is currently closed. Could I still get your details so we can follow up?"
        - If OPEN: continue collecting and validating info, then say "Thank you! I'll forward your details to our team now."

        5. Ending Call:
        - End naturally if caller says "bye" or "thank you".
        - Ensure all required info collected and validated before ending.

        DATA CAPTURE & INTERNAL RULES:
        - Store: patientType, fullName, phoneNumber, email, reason, preferredDays, preferredTimes, servicesInterested, source.
        - Missing info → "Unknown".
        - Never reveal validation rules or backend logic to caller.
        - **Always speak directly to the caller using their first name naturally once known.**
          Do NOT refer to them in the third person.
          Example: Say "Bashi, may I have your phone number?" NOT "What might be the reason for Bashi's visit?"

        VALIDATION & CLARIFICATION:
        - If info invalid, ask naturally: "I might have misheard your email/phone — could you repeat that?"
        - Proceed only once required fields are valid.

        BEHAVIORAL GUIDELINES:
        - Calm, empathetic, human.
        - Use fillers naturally.
        - Do NOT interrupt caller.
        - Avoid phrases like "Let me check that"; transition naturally.
        - **Always address the caller in first person / direct speech.**
        - NEVER switch to third-person references for the caller.

        EXAMPLES OF GOOD RESPONSES:
        - "Hi there! Thanks for calling Brookline Progressive Dental. How can I help you today?"
        - "That's wonderful! We love welcoming new patients."
        - "Can I have your full name please?"
        - "I might have misheard your email — could you repeat that for me?"
        - "Thank you! I'll forward your details to our team now."
        """

        # --- send settings to Deepgram ---
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
                    "provider": {"type": "open_ai", "model": "gpt-4o", "temperature": 0.7},
                    "prompt": system_prompt,
                },
                "speak": {"provider": {"type": "deepgram", "model": "aura-2-thalia-en"}},
                "greeting": "Hi there! Thanks for calling Brookline Progressive Dental. How can I help you today?"
            },
        }

        await sts_ws.send(json.dumps(config_message))

        # --- Simplified sender loop ---
        async def sts_sender(sts_ws):
            while True:
                chunk = await audio_queue.get()
                try:
                    await sts_ws.send(chunk)
                except websockets.exceptions.ConnectionClosedOK:
                    print("⚠️ STS sender closed")
                    break

        # --- Simplified receiver loop (no FunctionCall handling) ---
        async def sts_receiver(sts_ws):
            streamsid = await streamsid_queue.get()
            async for message in sts_ws:
                if isinstance(message, str):
                    decoded = json.loads(message)
                    msg_type = decoded.get("type")

                    if msg_type == "SettingsApplied":
                        print("✅ Deepgram settings applied")
                        continue

                    if msg_type == "UserStartedSpeaking":
                        clear_message = {"event": "clear", "streamSid": streamsid}
                        await twilio_ws.send(json.dumps(clear_message))
                        continue

                    # Send text responses from AI as TTS
                    if msg_type == "ConversationText":
                        content = decoded.get("content")
                        print(f"\n🤖 AI: {content}\n")
                        dialog_history.append({"role": "ai", "text": content})
                        media_message = {
                            "event": "media",
                            "streamSid": streamsid,
                            "media": {"payload": base64.b64encode(content.encode()).decode("ascii")},
                        }
                        await twilio_ws.send(json.dumps(media_message))

                else:
                    # Binary audio already handled by Deepgram
                    raw_mulaw = message
                    media_message = {
                        "event": "media",
                        "streamSid": streamsid,
                        "media": {"payload": base64.b64encode(raw_mulaw).decode("ascii")},
                    }
                    await twilio_ws.send(json.dumps(media_message))

        # --- Twilio receiver remains the same ---
        async def twilio_receiver(twilio_ws):
            BUFFER_SIZE = 20 * 160
            inbuffer = bytearray(b"")
            async for message in twilio_ws:
                try:
                    data = json.loads(message)
                    # Capture streamSid
                    if data.get("event") == "start":
                        streamsid_queue.put_nowait(data["start"]["streamSid"])

                    # If Twilio sends transcription
                    if data.get("event") == "transcript":
                        transcript = data.get("text")
                        print(f"\n🗣 User: {transcript}\n")
                        dialog_history.append({"role": "user", "text": transcript})

                    # Handle raw audio (existing)
                    if data.get("event") == "media" and data["media"]["track"] == "inbound":
                        chunk = base64.b64decode(data["media"]["payload"])
                        inbuffer.extend(chunk)
                    while len(inbuffer) >= BUFFER_SIZE:
                        audio_queue.put_nowait(inbuffer[:BUFFER_SIZE])
                        inbuffer = inbuffer[BUFFER_SIZE:]

                    # Save dialog at end of call
                    if data.get("event") == "stop":
                        filename = f"call_logs/{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        os.makedirs("call_logs", exist_ok=True)
                        with open(filename, "w") as f:
                            json.dump(dialog_history, f, indent=2)
                        print(f"\n💾 Dialog saved to {filename}\n")

                except Exception as e:
                    print(f"❌ Twilio receiver error: {e}")
                    break

        await asyncio.wait([
            asyncio.ensure_future(sts_sender(sts_ws)),
            asyncio.ensure_future(sts_receiver(sts_ws)),
            asyncio.ensure_future(twilio_receiver(twilio_ws)),
        ])

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

