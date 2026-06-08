import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL или SUPABASE_KEY не указаны в .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def save_message_supabase(
    telegram_id,
    chat_id,
    chat_name,
    sender_name,
    text
):
    data = {
        "telegram_id": telegram_id,
        "chat_id": chat_id,
        "chat_name": chat_name,
        "sender_name": sender_name,
        "text": text,
    }

    supabase.table("telegram_messages").insert(data).execute()


def save_lead_supabase(
    telegram_id,
    chat_id,
    chat_name,
    sender_name,
    text,
    topic,
    priority,
    needs_manual_reply
):
    data = {
        "telegram_id": telegram_id,
        "chat_id": chat_id,
        "chat_name": chat_name,
        "sender_name": sender_name,
        "text": text,
        "topic": topic,
        "priority": priority,
        "needs_manual_reply": needs_manual_reply,
    }

    supabase.table("telegram_leads").insert(data).execute()