import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from supabase import create_client


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def load_env_manual(path):
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


if ENV_PATH.exists():
    load_env_manual(ENV_PATH)

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def load_accounts():
    result = (
        supabase.table("telegram_accounts")
        .select("*")
        .eq("status", "active")
        .eq("active", True)
        .not_.is_("session_string", "null")
        .execute()
    )
    return result.data or []


async def save_message(
    account,
    dialog_id,
    dialog_username,
    dialog_name,
    direction,
    text,
    message_date
):
    if not text:
        return

    data = {
        "owner_id": str(account.get("owner_id") or "default_owner"),

        "account_session_name": account.get("session_name"),
        "account_username": account.get("username"),

        "ad_name": account.get("ad_name"),
        "pc_name": account.get("pc_name"),
        "operator_name": account.get("operator_name"),
        "phone": account.get("phone"),

        "dialog_id": str(dialog_id),
        "dialog_username": dialog_username,
        "dialog_name": dialog_name,

        "direction": direction,
        "text": text,

        "message_date": (
            message_date.isoformat()
            if message_date
            else datetime.now(timezone.utc).isoformat()
        ),

        "local_time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    }

    try:
        supabase.table("telegram_messages").insert(data).execute()

        print(
            f"✅ [{account.get('username')}] "
            f"{direction} @{dialog_username}: {text[:80]}"
        )

    except Exception as e:
        print("❌ SUPABASE SAVE ERROR:", e)


async def start_account(account):
    session_string = account.get("session_string")
    username = account.get("username") or account.get("phone") or account.get("session_name")

    print(f"🔌 Подключаю аккаунт: {username}", flush=True)

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

    await client.connect()

    if not await client.is_user_authorized():
        print(f"❌ Аккаунт {username} не авторизован", flush=True)
        return None

    print(f"✅ Аккаунт авторизован: {username}", flush=True)

    me = await client.get_me()
    print(f"🟢 Аккаунт запущен из Supabase: @{me.username or me.id}")

    @client.on(events.NewMessage(incoming=True))
    async def incoming_handler(event):
        try:
            sender = await event.get_sender()
            dialog_id = sender.id if sender else event.chat_id
            dialog_username = getattr(sender, "username", None) or str(dialog_id)

            first_name = getattr(sender, "first_name", "") or ""
            last_name = getattr(sender, "last_name", "") or ""
            dialog_name = f"{first_name} {last_name}".strip() or dialog_username

            await save_message(
                account=account,
                dialog_id=dialog_id,
                dialog_username=dialog_username,
                dialog_name=dialog_name,
                direction="incoming",
                text=event.raw_text,
                message_date=event.message.date,
            )

        except Exception as e:
            print(f"❌ INCOMING ERROR [{username}]:", e)

    @client.on(events.NewMessage(outgoing=True))
    async def outgoing_handler(event):
        try:
            chat = await event.get_chat()
            dialog_id = getattr(chat, "id", event.chat_id)
            dialog_username = getattr(chat, "username", None) or str(dialog_id)

            first_name = getattr(chat, "first_name", "") or ""
            last_name = getattr(chat, "last_name", "") or ""
            title = getattr(chat, "title", "") or ""
            dialog_name = title or f"{first_name} {last_name}".strip() or dialog_username

            await save_message(
                account=account,
                dialog_id=dialog_id,
                dialog_username=dialog_username,
                dialog_name=dialog_name,
                direction="outgoing",
                text=event.raw_text,
                message_date=event.message.date,
            )

        except Exception as e:
            print(f"❌ OUTGOING ERROR [{username}]:", e)

    return client


async def main():
    print("✅ multworker.py запущен", flush=True)
    print("🔎 Загружаю Telegram-аккаунты из Supabase...", flush=True)

    accounts = load_accounts()

    if not accounts:
        print("❌ В Supabase нет active аккаунтов с session_string", flush=True)
        return

    print(f"🔎 Найдено аккаунтов: {len(accounts)}", flush=True)

    clients = []

    for account in accounts:
        username = account.get("username") or account.get("phone") or account.get("session_name")

        try:
            print(f"🔌 Пробую запустить аккаунт: {username}", flush=True)

            client = await start_account(account)

            if client:
                clients.append(client)
                print(f"✅ Аккаунт добавлен в прослушку: {username}", flush=True)
            else:
                print(f"⚠️ Аккаунт не вернул client: {username}", flush=True)

        except Exception as e:
            print(f"❌ Не смог запустить аккаунт {username}: {e}", flush=True)

    if not clients:
        print("❌ Ни один аккаунт не запустился", flush=True)
        return

    print("✅ Все доступные аккаунты слушаются. Жду сообщения...", flush=True)

    await asyncio.gather(
        *[client.run_until_disconnected() for client in clients]
    )


if __name__ == "__main__":
    asyncio.run(main())
