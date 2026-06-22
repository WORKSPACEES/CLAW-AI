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

# ─── report_channels ──────────────────────────────────────────────────────────

def save_bot_channels(owner_user_id: str, channels: list):
    for ch in channels:
        supabase.table("report_channels").upsert({
            "owner_user_id": str(owner_user_id),
            "session_name": "__bot__",
            "channel_id": str(ch["channel_id"]),
            "channel_title": ch.get("channel_title", ""),
        }, on_conflict="session_name,channel_id").execute()


def get_bot_channels(owner_user_id: str) -> list:
    result = (
        supabase.table("report_channels")
        .select("channel_id, channel_title")
        .eq("session_name", "__bot__")
        .execute()
    )
    return result.data or []


def link_account_to_channel(owner_user_id: str, session_name: str, channel_id: str, channel_title: str):
    supabase.table("report_channels").upsert({
        "owner_user_id": str(owner_user_id),
        "session_name": session_name,
        "channel_id": str(channel_id),
        "channel_title": channel_title,
    }, on_conflict="session_name,channel_id").execute()


def get_channel_for_account(session_name: str):
    result = (
        supabase.table("report_channels")
        .select("channel_id, channel_title")
        .eq("session_name", session_name)
        .neq("session_name", "__bot__")
        .limit(1)
        .execute()
    )
    data = result.data or []
    return data[0] if data else None


def get_all_account_channels() -> list:
    result = (
        supabase.table("report_channels")
        .select("session_name, channel_id, channel_title")
        .neq("session_name", "__bot__")
        .execute()
    )
    return result.data or []

def get_timer_settings(channel_id: str):
    """Возвращает настройки таймера для канала или None если не заданы."""
    try:
        result = (
            supabase.table("report_timer_settings")
            .select("*")
            .eq("channel_id", channel_id)
            .execute()
        )
        if result.data:
            return result.data[0]
    except Exception as e:
        print("⚠️ get_timer_settings ERROR:", e)
    return None


def set_timer_settings(channel_id: str, channel_title: str, day_hour: int, day_minute: int, night_hour: int, night_minute: int):
    """Сохраняет настройки таймера для канала."""
    try:
        supabase.table("report_timer_settings").upsert({
            "channel_id": channel_id,
            "channel_title": channel_title,
            "day_hour": day_hour,
            "day_minute": day_minute,
            "night_hour": night_hour,
            "night_minute": night_minute,
            "updated_at": "now()",
        }, on_conflict="channel_id").execute()
        return True
    except Exception as e:
        print("⚠️ set_timer_settings ERROR:", e)
        return False


def get_all_timer_settings():
    """Возвращает настройки таймера для всех каналов."""
    try:
        result = (
            supabase.table("report_timer_settings")
            .select("*")
            .execute()
        )
        return result.data or []
    except Exception as e:
        print("⚠️ get_all_timer_settings ERROR:", e)
        return []

def remove_bot_channel(channel_id: str):
    """Удаляет канал из report_channels когда бот был удалён из него."""
    try:
        supabase.table("report_channels").delete().eq("channel_id", str(channel_id)).execute()
    except Exception as e:
        print("⚠️ remove_bot_channel ERROR:", e)
