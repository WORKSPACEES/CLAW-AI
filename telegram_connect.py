import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from supabase_db import supabase

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")

login_clients = {}


def make_session_name(owner_user_id, phone):
    clean_phone = phone.replace("+", "").replace(" ", "")
    return f"telegram_{owner_user_id}_{clean_phone}"


async def start_login(owner_user_id, phone, ad_name=None, pc_name=None, operator_name=None):
    session_name = make_session_name(owner_user_id, phone)

    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()

    sent = await client.send_code_request(phone)

    login_clients[owner_user_id] = {
        "client": client,
        "phone": phone,
        "session_name": session_name,
        "phone_code_hash": sent.phone_code_hash,
        "ad_name": ad_name,
        "pc_name": pc_name,
        "operator_name": operator_name,
    }


async def confirm_code(owner_user_id, code):
    data = login_clients.get(owner_user_id)

    if not data:
        return {
            "ok": False,
            "message": "Сначала напиши: подключить тг"
        }

    client = data["client"]

    try:
        await client.sign_in(
            phone=data["phone"],
            code=code,
            phone_code_hash=data["phone_code_hash"],
        )

        me = await client.get_me()
        session_string = client.session.save()

        supabase.table("telegram_accounts").upsert({
            "owner_user_id": str(owner_user_id),
            "owner_id": str(os.getenv("REPORT_CHAT_ID", "default_owner")),
            "session_name": data["session_name"],
            "session_string": session_string,
            "phone": data["phone"],
            "username": me.username,
            "first_name": me.first_name,
            "ad_name": data.get("ad_name"),
            "pc_name": data.get("pc_name"),
            "operator_name": data.get("operator_name"),
            "status": "active",
            "active": True,
        }, on_conflict="session_name").execute()

        await client.disconnect()
        del login_clients[owner_user_id]

        return {
            "ok": True,
            "message": f"✅ Telegram подключен и сохранён в Supabase: {me.first_name} / @{me.username}"
        }

    except Exception as e:
        return {
            "ok": False,
            "message": f"❌ Ошибка входа: {e}"
        }


def list_accounts(owner_user_id):
    result = (
        supabase.table("telegram_accounts")
        .select("*")
        .eq("owner_user_id", str(owner_user_id))
        .eq("status", "active")
        .eq("active", True)
        .execute()
    )

    return result.data or []

def normalize_phone(phone):
    return "".join(ch for ch in str(phone) if ch.isdigit())


def delete_account_by_phone(owner_user_id, phone):
    target_phone = normalize_phone(phone)

    result = (
        supabase.table("telegram_accounts")
        .select("*")
        .execute()
    )

    accounts = result.data or []

    found_accounts = []

    for acc in accounts:
        db_phone = normalize_phone(acc.get("phone"))

        if db_phone == target_phone:
            found_accounts.append(acc)

    if not found_accounts:
        return {
            "ok": False,
            "message": f"❌ Не нашёл сессию по номеру: {phone}"
        }

    for acc in found_accounts:
        session_name = acc.get("session_name")
        acc_phone = acc.get("phone")

        supabase.table("telegram_messages").delete().eq(
            "account_session_name", session_name
        ).execute()

        supabase.table("telegram_leads").delete().eq(
            "phone", acc_phone
        ).execute()

        supabase.table("telegram_accounts").update({
            "status": "deleted",
            "active": False,
            "session_string": None,
        }).eq("session_name", session_name).execute()

    return {
        "ok": True,
        "message": f"✅ Сессия удалена по номеру: {phone}\n🧹 Все сообщения этого аккаунта очищены из отчётов."
    }
