from flask import Flask, jsonify, request
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from dotenv import load_dotenv
import os

load_dotenv()
app = Flask(__name__)

@app.route("/token", methods=["POST"])
def generate_token():
    identity = request.json.get("identity", "web_caller")

    token = AccessToken(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_API_KEY"),
        os.getenv("TWILIO_API_SECRET"),
        identity=identity
    )

    voice_grant = VoiceGrant(
        outgoing_application_sid=os.getenv("TWILIO_TWIML_APP_SID"),
        incoming_allow=True
    )
    token.add_grant(voice_grant)

    return jsonify({"token": token.to_jwt().decode(), "identity": identity})


@app.route("/voice", methods=["POST"])
def voice_webhook():
    """Handles incoming calls via TwiML"""
    from twilio.twiml.voice_response import VoiceResponse
    response = VoiceResponse()
    response.say("Connecting your call to our Brookline Progressive Dental assistant.")
    response.dial().client("web_caller")
    return str(response), 200, {"Content-Type": "application/xml"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
