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
        system_prompt = build_receptionist_prompt(within_hours, current_time)

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
                    "functions": FUNCTION_DEFINITIONS,
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
                if isinstance(message, str):
                    decoded = json.loads(message)

                    # handle function calls from Deepgram
                    if decoded.get("type") == "FunctionCall":
                        fn_name = decoded.get("name")
                        params = decoded.get("parameters", {})
                        if fn_name in FUNCTION_MAP:
                            print(f"⚙️ Executing function: {fn_name}")
                            result = await FUNCTION_MAP[fn_name](params)
                            response = {
                                "type": "FunctionCallResponse",
                                "name": fn_name,
                                "result": result,
                            }
                            await sts_ws.send(json.dumps(response))
                        continue

                    # handle barge-in
                    if decoded.get("type") == "UserStartedSpeaking":
                        clear_message = {"event": "clear", "streamSid": streamsid}
                        await twilio_ws.send(json.dumps(clear_message))
                    continue

                # Binary audio (tts)
                raw_mulaw = message
                media_message = {
                    "event": "media",
                    "streamSid": streamsid,
                    "media": {"payload": base64.b64encode(raw_mulaw).decode("ascii")},
                }
                await twilio_ws.send(json.dumps(media_message))

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
