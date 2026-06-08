import asyncio
import os
from datetime import timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import User

from database import save_message
from supabase_db import save_message_supabase, save_lead_supabase
from ai import analyze_single_message_for_lead

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")

KYIV_TZ = ZoneInfo("Europe/Kyiv")
WORK_START_HOUR = 9
WORK_END_HOUR = 21


def is_work_time(dt):
    local_dt = dt.astimezone(KYIV_TZ)
    return WORK_START_HOUR <= local_dt.hour < WORK_END_HOUR, local_dt


async def main():
    client = TelegramClient("dialog_monitor_session", API_ID, API_HASH)

    @client.on(events.NewMessage())
    async def new_message_handler(event):
        chat = await event.get_chat()
        sender = await event.get_sender()

        # Только личные чаты. Группы, каналы, избранное — пропускаем.
        if not isinstance(chat, User):
            return

        # Избранное / Saved Messages пропускаем
        if getattr(chat, "is_self", False):
            return

        allowed_time, local_dt = is_work_time(event.message.date)

        # Берём только сообщения с 09:00 до 21:00 по Киеву
        if not allowed_time:
            return

        direction = "outgoing" if event.out else "incoming"

        sender_name = (
            getattr(sender, "first_name", None)
            or getattr(sender, "username", None)
            or "Без имени"
        )

        username = getattr(chat, "username", None)
        chat_title = (
            getattr(chat, "first_name", None)
            or getattr(chat, "username", None)
            or "Без имени"
        )

        text = event.raw_text or ""

        telegram_id = chat.id if chat else 0
        chat_id = chat.id if chat else 0

        print("=" * 40)
        print("ЛИЧНОЕ СООБЩЕНИЕ")
        print("НАПРАВЛЕНИЕ:", direction)
        print("ЧАТ:", chat_title)
        print("USERNAME:", username)
        print("ОТ:", sender_name)
        print("ВРЕМЯ КИЕВ:", local_dt.strftime("%Y-%m-%d %H:%M:%S"))
        print("ТЕКСТ:", text)

        try:
            save_message(
                telegram_id,
                chat_id,
                chat_title,
                sender_name,
                text
            )
            print("✅ SQLite: сообщение сохранено")
        except Exception as e:
            print("❌ SQLite ERROR:", e)

        try:
            save_message_supabase(
                telegram_id,
                chat_id,
                chat_title,
                sender_name,
                text
            )
            print("✅ Supabase: сообщение сохранено")
        except Exception as e:
            print("❌ Supabase ERROR:", e)

        # AI-анализ только входящих сообщений
        if direction == "incoming":
            try:
                lead_result = analyze_single_message_for_lead(
                    text=text,
                    chat_name=chat_title,
                    sender_name=sender_name
                )

                print("🤖 LEAD ANALYSIS:", lead_result)

                if lead_result.get("lead") is True:
                    save_lead_supabase(
                        telegram_id,
                        chat_id,
                        chat_title,
                        sender_name,
                        text,
                        lead_result.get("topic", "unknown"),
                        lead_result.get("priority", "low"),
                        lead_result.get("needs_manual_reply", False)
                    )

                    print("🔥 Лид сохранён в Supabase")
            except Exception as e:
                print("❌ LEAD ANALYSIS ERROR:", e)

    print("TELETHON WORKER STARTING...")

    await client.start()

    me = await client.get_me()
    print(f"✅ TELEGRAM ПОДКЛЮЧЕН: {me.first_name} / @{me.username}")
    print("Слушаю только личные чаты с 09:00 до 21:00 по Киеву...")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())