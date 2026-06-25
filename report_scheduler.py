import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from supabase_db import supabase, get_all_account_channels, get_all_timer_settings

from aiogram import Bot
from dialog_report import build_reports_by_accounts
from bot import report_keyboard


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

def get_last_sent_slot_for(slot_id: str) -> bool:
    """Возвращает True если этот slot_id уже был отправлен."""
    try:
        result = (
            supabase.table("scheduler_state")
            .select("id")
            .eq("id", slot_id)
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        print("⚠️ get_last_sent_slot_for ERROR:", e)
    return False


def set_last_sent_slot(slot_id: str):
    """Помечает slot_id как отправленный."""
    try:
        supabase.table("scheduler_state").upsert({
            "id": slot_id,
            "last_sent_slot": slot_id,
        }).execute()
    except Exception as e:
        print("⚠️ set_last_sent_slot ERROR:", e)


def get_all_due_slots(now):
    """
    Возвращает список (slot_id, slot_time, channel_id) для всех каналов
    у которых есть настройки таймера и чей слот уже наступил.
    Если для канала нет настроек — использует дефолт 9:00 / 21:00.
    """
    settings_list = get_all_timer_settings()

    # Если нет ни одной настройки — дефолтный режим (один канал REPORT_CHAT_ID)
    if not settings_list:
        settings_list = [{
            "channel_id": REPORT_CHAT_ID,
            "channel_title": "default",
            "day_hour": 21,
            "day_minute": 0,
            "night_hour": 9,
            "night_minute": 0,
        }]

    due = []
    for s in settings_list:
        channel_id = s["channel_id"]
        for hour, minute in [(s["day_hour"], s["day_minute"]), (s["night_hour"], s["night_minute"])]:
            slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < slot_time:
                slot_time -= timedelta(days=1)
            slot_id = f"{channel_id}__{slot_time.strftime('%Y-%m-%d_%H:%M')}"
            due.append((slot_id, slot_time, channel_id))

    return due



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


async def send_shift_report(report_time, target_channel_id=None):
    start_time, end_time, shift_name = get_shift_for_report(report_time)

    print("=" * 50)
    print("Собираю отчёт")
    print("Смена:", shift_name)
    print("Период:", start_time, "—", end_time)

    try:
        reports = build_reports_by_accounts(
            start_time=start_time,
            end_time=end_time,
            shift_name=shift_name,
            detailed=False,
        )

        account_channels = {
            row["session_name"]: row
            for row in get_all_account_channels()
        }

        if not reports:
            await bot.send_message(REPORT_CHAT_ID, "За этот период новых диалогов нет.")
            print("✅ Пустой отчёт отправлен в канал")
            return

        for report in reports:
            session_name = report["session_name"]
            channel = account_channels.get(session_name)

            target_chat = channel["channel_id"] if channel else (target_channel_id or REPORT_CHAT_ID)

            await bot.send_message(
                target_chat,
                report["text"],
                reply_markup=report_keyboard(session_name, start_time=start_time, end_time=end_time)
            )

            print(f"✅ Отчёт [{session_name}] → {target_chat}")

        print("✅ Отчёты по аккаунтам отправлены")

    except Exception as e:
        print("❌ Ошибка отправки отчёта:", e)
        await bot.send_message(REPORT_CHAT_ID, f"❌ Ошибка отчёта: {e}")


async def main():
    print("✅ report_scheduler.py запущен")
    print("Канал отчётов:", REPORT_CHAT_ID)

    while True:
        now = datetime.now(KYIV_TZ)
        due_slots = get_all_due_slots(now)

        for slot_id, slot_time, channel_id in due_slots:
            last_sent = get_last_sent_slot_for(slot_id)
            if not last_sent:
                print("=" * 50)
                print("🔔 Неотправленный слот:", slot_id)
                print("Канал:", channel_id)
                print("Сейчас:", now.strftime("%d.%m.%Y %H:%M:%S"))

                await send_shift_report(slot_time, channel_id)
                set_last_sent_slot(slot_id)

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
