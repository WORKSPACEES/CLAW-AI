import os
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
from supabase_db import supabase

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")

login_clients = {}


def normalize_phone(phone):
    return "".join(ch for ch in str(phone) if ch.isdigit())


def make_session_name(owner_user_id, phone):
    clean_phone = normalize_phone(phone)
    return f"telegram_{owner_user_id}_{clean_phone}"


async def start_login(owner_user_id, phone, ad_name=None, pc_name=None, operator_name=None):
    phone = str(phone).strip().replace(" ", "")

    if not phone.startswith("+"):
        return {
            "ok": False,
            "message": "❌ Номер должен быть в международном формате, например: +380..."
        }

    if owner_user_id in login_clients:
        old_client = login_clients[owner_user_id].get("client")

        try:
            await old_client.disconnect()
        except Exception:
            pass

        del login_clients[owner_user_id]

    session_name = make_session_name(owner_user_id, phone)

    client = TelegramClient(StringSession(), API_ID, API_HASH)

    try:
        await client.connect()

        sent = await client.send_code_request(phone)

        code_type = type(sent.type).__name__ if sent and sent.type else "unknown"
        next_type = type(sent.next_type).__name__ if getattr(sent, "next_type", None) else "none"

        print("✅ TELEGRAM CODE REQUEST SENT", flush=True)
        print("PHONE:", phone, flush=True)
        print("CODE TYPE:", code_type, flush=True)
        print("NEXT TYPE:", next_type, flush=True)
        print("PHONE CODE HASH:", sent.phone_code_hash, flush=True)

        login_clients[owner_user_id] = {
            "client": client,
            "phone": phone,
            "session_name": session_name,
            "phone_code_hash": sent.phone_code_hash,
            "ad_name": ad_name,
            "pc_name": pc_name,
            "operator_name": operator_name,
        }

        if "App" in code_type:
            where = (
                "📲 Telegram отправил код в приложение Telegram.\n\n"
                "Открой Telegram на этом номере и проверь официальный чат Telegram / 777000."
            )
        elif "Sms" in code_type:
            where = "📩 Telegram отправил код по SMS."
        elif "Call" in code_type:
            where = "📞 Telegram отправит код через звонок."
        else:
            where = f"📩 Telegram принял запрос кода. Тип отправки: {code_type}"

        return {
            "ok": True,
            "message": (
                "✅ Запрос кода отправлен.\n\n"
                f"{where}\n\n"
                "Теперь пришли код сюда."
            )
        }

    except Exception as e:
        print("❌ SEND CODE REQUEST ERROR:", repr(e), flush=True)

        try:
            await client.disconnect()
        except Exception:
            pass

        if owner_user_id in login_clients:
            del login_clients[owner_user_id]

        return {
            "ok": False,
            "message": f"❌ Telegram не отправил код.\n\nОшибка: {e}"
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

        if owner_user_id in login_clients:
            del login_clients[owner_user_id]

        return {
            "ok": True,
            "message": f"✅ Telegram подключен и сохранён в Supabase: {me.first_name} / @{me.username}"
        }

    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass

        if owner_user_id in login_clients:
            del login_clients[owner_user_id]

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

    deleted_count = 0

    for acc in found_accounts:
        session_name = acc.get("session_name")

        if not session_name:
            continue

        supabase.table("telegram_messages").delete().eq(
            "account_session_name", session_name
        ).execute()

        supabase.table("telegram_accounts").update({
            "status": "deleted",
            "active": False,
            "session_string": None,
        }).eq("session_name", session_name).execute()

        deleted_count += 1

    return {
        "ok": True,
        "message": (
            f"✅ Сессия удалена по номеру: {phone}\n"
            f"🧹 Очищены сообщения из отчётов.\n"
            f"Удалено аккаунтов: {deleted_count}"
        )
    }
