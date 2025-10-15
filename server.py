import asyncio
import base64
import json
import sys
import websockets
import ssl
import os
from dotenv import load_dotenv
from datetime import datetime
import pytz

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


def get_office_status():
    # Brookline Progressive Dental office hours: Mon-Fri 9am-5pm Eastern
    tz = pytz.timezone("America/New_York")
    now = datetime.now(tz)
    is_weekday = now.weekday() < 5
    within_hours = is_weekday and 9 <= now.hour < 17
    return within_hours, now.strftime("%A %I:%M %p")

async def twilio_handler(twilio_ws):
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()

    async with sts_connect() as sts_ws:
        within_hours, current_time = get_office_status()

        system_prompt = f"""
        You are a warm, natural, and slightly conversational dental office receptionist
        for **Brookline Progressive Dental**. You are speaking *live* on the phone with a caller,
        so your tone should always sound friendly, empathetic, and human — avoid robotic phrasing.
        It's fine to use small natural fillers like “uh”, “hmm”, or “okay” occasionally, but keep it subtle.

        Your goal is to handle calls smoothly and naturally, keeping track of context, while
        helping the caller with whatever they need, including collecting contact information
        and logging it appropriately.

        Current context:
        - Office hours: Monday-Friday, 9 a.m.-5 p.m. Eastern.
        - Current time is {current_time}, so the office is {'OPEN' if within_hours else 'CLOSED'}.

        Call flow guidance:
        1. Greeting: Warmly welcome the caller and ask if they are a new patient, an existing patient, 
        or calling for another reason.
        2. New patient: Express genuine excitement about welcoming them.
        3. Determine timing:
        - If the call is **off office hours**:
            - Politely inform them the office is currently closed.
            - Collect their **name, email, phone number, and reason for calling** if they are willing.
            - If the caller declines, capture the **phone number** and set name/email/reason as unknown.
        - If the call is **within office hours**:
            - Before forwarding to the front office, ask for **name, email, phone number, and reason for call**.
            - If the caller declines, forward the call to the front office and capture the **phone number** with name/email/reason as unknown.
        4. Forwarding: Once contact info is collected or the caller declines, naturally thank them
        and forward their call to the Brookline team.
        5. Office answers / appointment: If the office answers and an appointment is made, mark the call as completed.

        Data capture rules:
        - Always try to collect **name, email, phone number, reason for call**.
        - If any information is missing due to refusal, log what is available (e.g., phone number) and mark others as unknown.
        - Use polite, natural language to encourage sharing info without being pushy.

        Style tips:
        - Use the caller's name naturally whenever possible.
        - Avoid repeating phrases verbatim.
        - Respond dynamically to the caller's tone and needs.
        - Think of example phrases as tone references, not scripts to repeat.

        Your responses should always sound conversational, empathetic, and human while following this call flow.
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
                        "model": "gpt-4o-mini",
                        "temperature": 0.7,
                    },
                    "prompt": system_prompt,
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
            # we will wait until the twilio ws connection figures out the streamsid
            streamsid = await streamsid_queue.get()
            # for each sts result received, forward it on to the call
            async for message in sts_ws:
                if type(message) is str:
                    print(message)
                    # handle barge-in
                    decoded = json.loads(message)
                    if decoded['type'] == 'UserStartedSpeaking':
                        clear_message = {
                            "event": "clear",
                            "streamSid": streamsid
                        }
                        await twilio_ws.send(json.dumps(clear_message))

                    continue

                print(type(message))
                raw_mulaw = message

                # construct a Twilio media message with the raw mulaw (see https://www.twilio.com/docs/voice/twiml/stream#websocket-messages---to-twilio)
                media_message = {
                    "event": "media",
                    "streamSid": streamsid,
                    "media": {"payload": base64.b64encode(raw_mulaw).decode("ascii")},
                }

                # send the TTS audio to the attached phonecall
                await twilio_ws.send(json.dumps(media_message))

        async def twilio_receiver(twilio_ws):
            print("twilio_receiver started")
            # twilio sends audio data as 160 byte messages containing 20ms of audio each
            # we will buffer 20 twilio messages corresponding to 0.4 seconds of audio to improve throughput performance
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

                    # check if our buffer is ready to send to our audio_queue (and, thus, then to sts)
                    while len(inbuffer) >= BUFFER_SIZE:
                        chunk = inbuffer[:BUFFER_SIZE]
                        audio_queue.put_nowait(chunk)
                        inbuffer = inbuffer[BUFFER_SIZE:]
                except:
                    break

        # the async for loop will end if the ws connection from twilio dies
        # and if this happens, we should forward an some kind of message to sts
        # to signal sts to send back remaining messages before closing(?)
        # audio_queue.put_nowait(b'')

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
    # use this if using ssl
    # ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # ssl_context.load_cert_chain('cert.pem', 'key.pem')
    # server = await websockets.serve(router, '0.0.0.0', 443, ssl=ssl_context)

    # use this if not using ssl
    server = await websockets.serve(router, "0.0.0.0", 5000)
    print("✅ Server started on ws://localhost:5000")

    # Run forever
    await asyncio.Future()  # keeps the server alive


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Server stopped.")
