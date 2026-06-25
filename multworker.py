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
            f"{direction} @{dialog_username}: {text[:80]}",
            flush=True
        )

    except Exception as e:
        print("❌ SUPABASE SAVE ERROR:", e, flush=True)


async def check_deleted_chats(client, account):
    while True:
        try:
            result = (
                supabase.table("telegram_messages")
                .select("dialog_id")
                .eq("owner_id", str(account.get("owner_id") or "default_owner"))
                .eq("account_session_name", account.get("session_name"))
                .eq("chat_deleted", False)
                .execute()
            )

            dialog_ids = list(set(row["dialog_id"] for row in result.data or [] if row.get("dialog_id")))

            for dialog_id in dialog_ids:
                try:
                    await client.get_entity(int(dialog_id))
                except Exception:
                    supabase.table("telegram_messages").update({
                        "chat_deleted": True
                    }).eq("dialog_id", dialog_id).eq(
                        "account_session_name", account.get("session_name")
                    ).execute()

                    print(f"🗑 Чат удалён/недоступен: {dialog_id}", flush=True)

        except Exception as e:
            print("❌ CHECK DELETED CHATS ERROR:", e, flush=True)

        await asyncio.sleep(300)


async def start_account(account):
    session_string = account.get("session_string")
    username = account.get("username") or account.get("phone") or account.get("session_name")

    print(f"🔌 Подключаю аккаунт: {username}", flush=True)

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

    await client.connect()

    if not await client.is_user_authorized():
        print(f"❌ Аккаунт {username} не авторизован", flush=True)
        await client.disconnect()
        return None

    print(f"✅ Аккаунт авторизован: {username}", flush=True)

    me = await client.get_me()
    print(f"🟢 Аккаунт запущен из Supabase: @{me.username or me.id}", flush=True)

    print("🔎 Загружаю историю сообщений за текущую смену...", flush=True)

    from datetime import timezone
    from zoneinfo import ZoneInfo

    KYIV_TZ = ZoneInfo("Europe/Kyiv")
    now = datetime.now(KYIV_TZ)
    day_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    night_start = now.replace(hour=21, minute=0, second=0, microsecond=0)

    if day_start <= now < night_start:
        shift_start = day_start
    elif now >= night_start:
        shift_start = night_start
    else:
        shift_start = day_start.replace(day=day_start.day - 1) if day_start.day > 1 else day_start

    shift_start_utc = shift_start.astimezone(timezone.utc)

    loaded = 0

    async for dialog in client.iter_dialogs(limit=100):
        try:
            entity = dialog.entity
            from telethon.tl.types import User
            if not isinstance(entity, User):
                continue
            if getattr(entity, "is_self", False):
                continue

            dialog_id = entity.id
            if str(dialog_id) in ("777000", "42777"):
                continue

            dialog_username = getattr(entity, "username", None) or str(dialog_id)
            first_name = getattr(entity, "first_name", "") or ""
            last_name = getattr(entity, "last_name", "") or ""
            dialog_name = f"{first_name} {last_name}".strip() or dialog_username

            async for msg in client.iter_messages(entity, limit=50, offset_date=None):
                if not msg.date:
                    continue

                msg_date = msg.date.astimezone(KYIV_TZ)

                if msg_date < shift_start:
                    break

                if not msg.raw_text:
                    continue

                direction = "outgoing" if msg.out else "incoming"

                await save_message(
                    account=account,
                    dialog_id=dialog_id,
                    dialog_username=dialog_username,
                    dialog_name=dialog_name,
                    direction=direction,
                    text=msg.raw_text,
                    message_date=msg.date,
                )
                loaded += 1

        except Exception as e:
            print(f"❌ Ошибка загрузки истории диалога: {e}", flush=True)

    print(f"✅ Загружено исторических сообщений: {loaded}", flush=True)

    @client.on(events.NewMessage())
    async def message_handler(event):
        try:
            print("📩 NEW MESSAGE EVENT", flush=True)

            if event.is_private is not True:
                print("⏭ Не личный чат, пропускаю", flush=True)
                return

            chat = await event.get_chat()

            dialog_id = getattr(chat, "id", event.chat_id)

            if str(dialog_id) in ("777000", "42777"):
                print(f"⏭ Системное сообщение Telegram {dialog_id}, пропускаю", flush=True)
                return

            dialog_username = getattr(chat, "username", None) or str(dialog_id)

            first_name = getattr(chat, "first_name", "") or ""
            last_name = getattr(chat, "last_name", "") or ""
            title = getattr(chat, "title", "") or ""

            dialog_name = title or f"{first_name} {last_name}".strip() or dialog_username

            direction = "outgoing" if event.out else "incoming"

            await save_message(
                account=account,
                dialog_id=dialog_id,
                dialog_username=dialog_username,
                dialog_name=dialog_name,
                direction=direction,
                text=event.raw_text,
                message_date=event.message.date,
            )

        except Exception as e:
            print(f"❌ MESSAGE HANDLER ERROR [{username}]: {e}", flush=True)

    asyncio.create_task(check_deleted_chats(client, account))

    return client


async def main():
    print("✅ multworker.py запущен", flush=True)
    print("🔎 Загружаю Telegram-аккаунты из Supabase...", flush=True)

    accounts = load_accounts()

    if not accounts:
        print("❌ В Supabase нет active аккаунтов с session_string", flush=True)
        print("⏳ Жду 60 секунд и проверю снова...", flush=True)
        await asyncio.sleep(60)
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
