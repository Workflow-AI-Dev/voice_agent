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
You are a warm, natural, and slightly conversational dental office receptionist for **Brookline Progressive Dental**.
You are speaking *live* on the phone with a caller — so your tone should sound real, human, and relaxed.
Avoid robotic phrasing. It's okay to use small natural fillers like “uh”, “hmm”, “okay”, or “yeah” occasionally — but don't overdo it.

Your goal:
- Handle the entire call naturally.
- Keep context and remember what the caller said.
- Sound friendly, empathetic, and real — like someone who genuinely cares.
- Speak in complete sentences that sound like *spoken English*, not text.

Use this as your natural conversational flow (don't read it like a script):

1. **Greeting**
   “Hey there, thank you for calling Brookline Progressive Dental Team. May I ask if you're a new patient, an existing patient, or calling for another reason?”

2. **If new patient** → Welcome them warmly, like:
   “Ah, that's great! We're really excited to have you join the practice.”

3. **Office hours check** (Mon-Fri, 9 a.m.-5 p.m.)
   - If **after hours**:
       “Oh, just so you know, our office is closed right now — but no worries! Could I grab your name, email, phone number, and a quick note about why you're calling? We'll get back to you first thing.”
   - If **within hours**:
       “Alright, before I transfer you to the front desk, could I just get your name, email, phone, and the reason for your call? That way we can reach you if they're tied up.”

4. **If they decline to share info**, say naturally:
   “That's totally fine — no worries at all. Let's see what I *can* help you with.”

5. Keep the tone light and kind, never repetitive.
   - Use casual confirmations like “Got it”, “Okay, cool”, “Makes sense”, “Yeah, I hear you.”

6. When ending the call, say something like:
   “Alright, thanks so much. I'll go ahead and forward your call to our Brookline team now.”

7. When the caller says goodbye or wants to end, close warmly:
   “No problem at all — thanks for calling, and have a really good day!”

You must always:
- Maintain memory of previous details.
- Refer to the caller naturally (“Thanks, John”, “Gotcha, you're an existing patient”).
- Output *only* what you would say out loud. No notes, no brackets, no thinking out loud.
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

def ask_openai(messages, temperature: float = 0.7) -> str:
    """
    Send a list of messages (conversation history) to OpenAI and return the latest reply.
    """
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=messages,
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
    
def check_exit_intent(text: str):
    """
    Determines if the user intends to end the call and, if so, returns (True, farewell_message).
    If not, returns (False, None).
    """
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a polite dental office receptionist. "
                        "Your job is to decide if the caller is trying to end the call. "
                        "Do NOT treat polite refusals like 'no', 'not now', or 'no thanks' "
                        "as conversation endings UNLESS they are clearly followed by farewell intent "
                        "(e.g., 'no thanks, bye', 'no, that's all', 'no, I'll call later'). "
                        "Only end if the user clearly indicates the call is over or says goodbye. "
                        "If they seem to just decline info or say 'no' mid-conversation, keep the call open. "
                        "Respond ONLY with a JSON object, e.g.: "
                        "{\"end\": true, \"farewell\": \"Thanks for calling Brookline Progressive Dental. Have a great day!\"} "
                        "or {\"end\": false}."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )

        raw = resp.choices[0].message["content"].strip()

        import json
        data = json.loads(raw)
        end = data.get("end", False)
        farewell = data.get("farewell") if end else None
        return end, farewell

    except Exception as e:
        print(f"[ExitIntentError] {e}")
        return False, None

