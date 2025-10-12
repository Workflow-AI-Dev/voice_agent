import io
import os
import sounddevice as sd
import soundfile as sf
import numpy as np
import wave
import tempfile
import utils
from deepgram import FileSource
from datetime import datetime
import json
from io import BytesIO

SAMPLE_RATE = 16000
CHANNELS = 1
DURATION = 5  

def is_office_hours():
    now = datetime.now()
    weekday = now.weekday()  # Monday=0, Sunday=6
    hour = now.hour
    return (0 <= weekday <= 4) and (9 <= hour < 17)

def record_audio(duration=DURATION, sample_rate=SAMPLE_RATE):
    print("🎤 Speak now...")
    audio_data = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=CHANNELS, dtype='int16')
    sd.wait()
    print("✅ Recorded.")
    # Save to in-memory WAV
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data.tobytes())
    return buf.getvalue()

def play_audio(wav_bytes):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(wav_bytes)
        tmp.flush()
        sd.play(sd.read(tmp.name)[0], samplerate=SAMPLE_RATE)
        sd.wait()

def main():
    print("🗣 Voice assistant is ready. Press Ctrl+C to quit.\n")
    while True:
        try:
            # Step 1: Capture mic input
            audio_bytes = record_audio()
            payload: FileSource = {"buffer": audio_bytes}

            # Step 2: Transcribe
            transcript_json = utils.get_transcript(payload)
            transcribed_text = transcript_json["results"]["channels"][0]["alternatives"][0]["transcript"]
            print(f"\n👤 You said: {transcribed_text}")

            if not transcribed_text.strip():
                continue

            # Step 3: Get LLM response
            office_status = "within office hours" if is_office_hours() else "after office hours"
            prompt = f"The current time is {office_status}. Caller said: {transcribed_text}"
            response_text = utils.ask_openai(prompt)
            print(f"🤖 Assistant: {response_text}")

            # Step 4: Generate speech (in memory)
            payload = {"text": response_text}
            out_file = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name
            utils.deepgram.speak.rest.v("1").save(out_file, payload, utils.speak_options)

            # Step 5: Play directly
            print("🎧 Speaking...")
            audio_data, fs = sf.read(out_file)
            sd.play(audio_data, samplerate=fs)
            sd.wait()

        except KeyboardInterrupt:
            print("\n👋 Exiting.")
            break

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "caller_text": transcribed_text,
        "assistant_reply": response_text,
        "office_hours": office_status,
    }
    os.makedirs("logs", exist_ok=True)
    with open("logs/conversation_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

if __name__ == "__main__":
    main()
