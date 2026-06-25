import os
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from dialog_report import build_reports_by_accounts
from supabase_db import get_all_account_channels

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


def report_keyboard_with_time(session_name, start_time, end_time):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    start_str = start_time.strftime("%Y%m%dT%H%M")
    end_str = end_time.strftime("%Y%m%dT%H%M")

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📖 Развёрнутый отчёт",
                    callback_data=f"full_report_account:{session_name}:{start_str}:{end_str}"
                )
            ]
        ]
    )


def get_all_shifts_for_last_days(days=7):
    """
    Возвращает список всех смен за последние N дней.
    Каждая смена: (shift_name, start_time, end_time)
    Порядок от старых к новым.
    """
    now = datetime.now(KYIV_TZ)
    shifts = []

    for i in range(days, -1, -1):
        day = now - timedelta(days=i)

        # Дневная смена: 09:00 — 21:00
        day_start = day.replace(hour=9, minute=0, second=0, microsecond=0)
        day_end = day.replace(hour=21, minute=0, second=0, microsecond=0)

        # Ночная смена: 21:00 — 09:00 следующего дня
        night_start = day.replace(hour=21, minute=0, second=0, microsecond=0)
        night_end = (day + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

        # Не добавляем смены которые ещё не завершились
        if day_end <= now:
            shifts.append(("Дневная смена", day_start, day_end))

        if night_end <= now:
            shifts.append(("Ночная смена", night_start, night_end))

    return shifts


async def restore_all_reports():
    print("🔁 Восстановление отчётов за последние 7 дней...", flush=True)

    shifts = get_all_shifts_for_last_days(days=7)
    account_channels = {
        row["session_name"]: row
        for row in get_all_account_channels()
    }

    print(f"📅 Смен для обработки: {len(shifts)}", flush=True)

    for shift_name, start_time, end_time in shifts:
        label = f"{shift_name} {start_time.strftime('%d.%m %H:%M')} — {end_time.strftime('%H:%M')}"
        print(f"\n{'='*50}", flush=True)
        print(f"📊 {label}", flush=True)

        try:
            reports = build_reports_by_accounts(
                start_time=start_time,
                end_time=end_time,
                shift_name=shift_name,
                detailed=False,
            )

            if not reports:
                print(f"⏭ Нет данных за {label}", flush=True)
                continue

            for report in reports:
                session_name = report["session_name"]
                channel = account_channels.get(session_name)
                target_chat = channel["channel_id"] if channel else REPORT_CHAT_ID

                await bot.send_message(
                    target_chat,
                    report["text"],
                    reply_markup=report_keyboard_with_time(session_name, start_time, end_time)
                )

                print(f"✅ Отправлен [{session_name}] → {target_chat}", flush=True)

                # Небольшая пауза чтобы не словить флуд-лимит Telegram
                await asyncio.sleep(1)

        except Exception as e:
            print(f"❌ Ошибка смены {label}: {e}", flush=True)

    print("\n✅ Восстановление завершено!", flush=True)

    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(restore_all_reports())
