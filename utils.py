# utils.py
import os
import json
from dotenv import load_dotenv
from deepgram import DeepgramClient, PrerecordedOptions, SpeakOptions
import openai

load_dotenv()

DG_API_KEY = os.getenv("DG_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not DG_API_KEY or not OPENAI_API_KEY:
    raise ValueError("Please set DG_API_KEY and OPENAI_API_KEY in your environment (.env)")


# Initialize clients
deepgram = DeepgramClient(DG_API_KEY)

# For the openai python package, we configure the api key
openai.api_key = OPENAI_API_KEY

# System prompt for the LLM
system_prompt = """
You are a friendly, professional dental office assistant for **Brookline Progressive Dental**.
Your job is to handle incoming phone calls.

Follow this call flow strictly:

1. Greet the caller:
   - "Thank you for calling Brookline Progressive Dental Team. May I ask if you are a new patient, an existing patient, or calling for other reasons?"

2. If the caller says they're a **new patient**:
   - Say: "We’re very excited to welcome you as a new patient to our practice!"

3. Then check **office hours**:
   - If the current time is outside business hours (Mon–Fri 9am–5pm):
       > "Our office is currently closed. May I please have your name, email, phone number, and the reason for your call? We'll call you back as soon as possible."
       - If caller refuses to give info, record phone number (if available) and mark name as “unknown”.
       - After collecting info, say: "Thank you! We’ll reach out to you promptly once we reopen."
   - If within office hours:
       > "Before I forward your call to the front office, may I have your name, email, phone number, and the reason for your call? We'll make sure to reach you if the front office is busy."
       - If caller refuses info, still forward the call (record phone number, name='unknown').

4. If the caller is **existing patient**:
   - Ask reason for the call (billing, appointment, etc.)
   - Route accordingly.

5. If caller says “other reason”:
   - Collect brief description and contact info if possible.

6. Always end with:
   - "Thank you! Forwarding your call to the Brookline team now."

Your goal:
- Be concise and warm.
- Stay in role (dental office voice agent).
- Capture structured data from the conversation:
  *patient_type*, *name*, *email*, *phone*, *reason*, *after_hours* (True/False).
- If information is missing, ask politely for it.
"""


# Deepgram transcription options 
text_options = PrerecordedOptions(
    model="nova-2",
    language="en",
    summarize="v2",
    topics=True,
    intents=True,
    smart_format=True,
    sentiment=True,
)

# Deepgram TTS options 
speak_options = SpeakOptions(
    model="aura-asteria-en",
    encoding="linear16",
    container="wav",
)

def ask_openai(prompt: str, temperature: float = 0.7) -> str:
    """
    Send a prompt (transcribed user text) to OpenAI and return the assistant reply.
    Uses chat completions via the openai library.
    """
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            max_tokens=500,
        )
        return resp.choices[0].message["content"].strip()
    except Exception as e:
        return f"OpenAI error: {e}"

def get_transcript(payload, options=text_options):
    """
    Returns the Deepgram transcription JSON for the provided payload.
    payload: a FileSource dict like {"buffer": <bytes>}
    """
    try:
        response = deepgram.listen.rest.v("1").transcribe_file(payload, options).to_json()
        return json.loads(response)
    except Exception as e:
        raise RuntimeError(f"Deepgram transcription failed: {e}")

def get_topics(transcript_json):
    """
    Return a set of unique topics found in the Deepgram transcript JSON.
    """
    topics = set()
    try:
        segments = transcript_json.get("results", {}).get("topics", {}).get("segments", [])
        for segment in segments:
            for topic in segment.get("topics", []):
                topics.add(topic.get("topic"))
    except Exception:
        pass
    return topics

def get_summary(transcript_json):
    """
    Return the short summary string from the transcript (if available).
    """
    return transcript_json.get("results", {}).get("summary", {}).get("short", "")

def save_speech_summary(text: str, filename: str = "output.wav", options=speak_options):
    """
    Use Deepgram TTS to synthesize 'text' to 'filename'. Returns the API response JSON.
    """
    try:
        payload = {"text": text}
        response = deepgram.speak.rest.v("1").save(filename, payload, options)
        return response.to_json()
    except Exception as e:
        raise RuntimeError(f"Deepgram TTS failed: {e}")
