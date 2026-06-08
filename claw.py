import requests

CLAW_API_URL = "http://127.0.0.1:8000/analyze"


def send_to_claw(text, chat_name="", sender_name=""):
    payload = {
        "text": text,
        "chat_name": chat_name,
        "sender_name": sender_name,
    }

    try:
        response = requests.post(CLAW_API_URL, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }