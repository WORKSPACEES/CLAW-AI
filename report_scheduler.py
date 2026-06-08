import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from supabase_db import supabase

from aiogram import Bot
from dialog_report import build_report


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

KYIV_TZ = ZoneInfo("Europe/Kyiv")


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

BOT_TOKEN = os.getenv("BOT_TOKEN")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не найден в .env")

if not REPORT_CHAT_ID:
    raise RuntimeError("❌ REPORT_CHAT_ID не найден в .env")


bot = Bot(token=BOT_TOKEN)


def get_next_report_time():
    now = datetime.now(KYIV_TZ)

    today_9 = now.replace(hour=9, minute=0, second=0, microsecond=0)
    today_21 = now.replace(hour=21, minute=0, second=0, microsecond=0)

    if now < today_9:
        return today_9

    if now < today_21:
        return today_21

    return today_9 + timedelta(days=1)


def get_shift_for_report(report_time):
    report_time = report_time.astimezone(KYIV_TZ)

    if report_time.hour == 9:
        end_time = report_time.replace(hour=9, minute=0, second=0, microsecond=0)
        start_time = end_time - timedelta(hours=12)
        shift_name = "Ночная смена"
    else:
        end_time = report_time.replace(hour=21, minute=0, second=0, microsecond=0)
        start_time = end_time - timedelta(hours=12)
        shift_name = "Дневная смена"

    return start_time, end_time, shift_name


async def send_shift_report(report_time):
    start_time, end_time, shift_name = get_shift_for_report(report_time)

    print("=" * 50)
    print("Собираю отчёт")
    print("Смена:", shift_name)
    print("Период:", start_time, "—", end_time)

    try:
        report = build_report(
            start_time=start_time,
            end_time=end_time,
            shift_name=shift_name,
        )

        await bot.send_message(REPORT_CHAT_ID, report)
        print("✅ Отчёт отправлен в канал")

        clear_report_cache(start_time, end_time)
        print("🧹 Кэш сообщений за смену очищен из Supabase")

    except Exception as e:
        print("❌ Ошибка отправки отчёта:", e)
        await bot.send_message(REPORT_CHAT_ID, f"❌ Ошибка отчёта: {e}")


async def main():
    print("✅ report_scheduler.py запущен")
    print("Канал отчётов:", REPORT_CHAT_ID)

    while True:
        next_time = get_next_report_time()
        now = datetime.now(KYIV_TZ)

        wait_seconds = (next_time - now).total_seconds()

        print("=" * 50)
        print("Сейчас:", now.strftime("%d.%m.%Y %H:%M:%S"))
        print("Следующий отчёт:", next_time.strftime("%d.%m.%Y %H:%M:%S"))
        print("Ждать секунд:", int(wait_seconds))

        await asyncio.sleep(max(1, wait_seconds))

        await send_shift_report(next_time)

        await asyncio.sleep(60)


def clear_report_cache(start_time, end_time):
    result = (
        supabase.table("telegram_messages")
        .delete()
        .gte("message_date", start_time.isoformat())
        .lt("message_date", end_time.isoformat())
        .execute()
    )

    return result

if __name__ == "__main__":
    asyncio.run(main())
