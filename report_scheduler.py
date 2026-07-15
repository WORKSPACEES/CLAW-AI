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
    settings_list = get_all_timer_settings()

    if not settings_list:
        settings_list = [{
            "channel_id": REPORT_CHAT_ID,
            "channel_title": "default",
            "day_hour": 20,
            "day_minute": 40,
            "night_hour": 8,
            "night_minute": 40,
            "poll_hour": 20,
            "poll_minute": 30,
        }]

    due = []
    for s in settings_list:
        channel_id = s["channel_id"]
        poll_hour = s.get("poll_hour", 20)
        poll_minute = s.get("poll_minute", 30)

        # Вычисляем poll_slot_id для привязки к отчётам
        poll_time = now.replace(hour=poll_hour, minute=poll_minute, second=0, microsecond=0)
        if now < poll_time:
            poll_time -= timedelta(days=1)
        poll_slot_id = f"{channel_id}__poll__{poll_time.strftime('%Y-%m-%d_%H:%M')}"

        # Слоты отчёта (день и ночь)
        for hour, minute in [(s["day_hour"], s["day_minute"]), (s["night_hour"], s["night_minute"])]:
            slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < slot_time:
                slot_time -= timedelta(days=1)
            slot_id = f"{channel_id}__{slot_time.strftime('%Y-%m-%d_%H:%M')}"
            due.append((slot_id, slot_time, channel_id, "report", poll_slot_id))

        # Слот опроса операторов
        due.append((poll_slot_id, poll_time, channel_id, "poll", None))

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


async def send_shift_report(report_time, target_channel_id=None, poll_slot_id=None):
    start_time, end_time, shift_name = get_shift_for_report(report_time)

    print("=" * 50)
    print("Собираю отчёт")
    print("Смена:", shift_name)
    print("Период:", start_time, "—", end_time)

    # slot_id для поиска статистики операторов — берём из poll слота
    slot_id = poll_slot_id or f"{target_channel_id}__poll__{report_time.strftime('%Y-%m-%d_%H:%M')}"

    # ── Собираем статистику операторов из БД ─────────────────────────────────
    try:
        stats_result = supabase.table("operator_stats").select("*").eq("shift_slot", slot_id).execute()
        operator_stats = {row["pc_name"]: row for row in (stats_result.data or [])}
    except Exception as e:
        print("❌ Ошибка загрузки operator_stats:", e)
        operator_stats = {}

    # ── Строим и отправляем отчёты ───────────────────────────────────────────
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

            # Получаем pc_name для этого аккаунта
            acc_result = supabase.table("telegram_accounts").select("pc_name").eq("session_name", session_name).limit(1).execute()
            pc_name = (acc_result.data or [{}])[0].get("pc_name", "-") if acc_result.data else "-"

            # Добавляем статистику оператора (всегда, даже если 0)
            op_stat = operator_stats.get(pc_name) or {}
            report_text = report["text"]
            report_text += (
                f"\n\nЗаходы: {op_stat.get('zahody', 0)} | "
                f"Брони: {op_stat.get('broni', 0)} | "
                f"Развороты: {op_stat.get('razvoroty', 0)}"
            )

            await bot.send_message(
                int(target_chat),
                report_text,
                reply_markup=report_keyboard(session_name)
            )

            print(f"✅ Отчёт [{session_name}] → {target_chat}")

        print("✅ Отчёты по аккаунтам отправлены")

    except Exception as e:
        print("❌ Ошибка отправки отчёта:", e)
        await bot.send_message(int(REPORT_CHAT_ID), f"❌ Ошибка отчёта: {e}")


async def poll_operators(slot_id, channel_id):
    """Опрашивает операторов — пишет им кнопки."""
    try:
        ops_result = supabase.table("operators").select("*").eq("active", True).execute()
        operators = ops_result.data or []

        if not operators:
            print("⚠️ Нет активных операторов для опроса")
            return

        from bot import operator_poll_state, build_operator_keyboard

        for op in operators:
            tg_id = op.get("telegram_id")
            pc_name = op.get("pc_name", "-")
            username = op.get("username", "")

            if not tg_id:
                continue

            operator_poll_state[tg_id] = {
                "zahody": 0,
                "broni": 0,
                "razvoroty": 0,
                "waiting_for": None,
                "slot": slot_id,
                "pc_name": pc_name,
            }

            try:
                await bot.send_message(
                    chat_id=tg_id,
                    text=(
                        f"📊 Смена заканчивается!\n"
                        f"Заполни статистику по ПК: {pc_name}\n\n"
                        "Нажми кнопку и введи количество:"
                    ),
                    reply_markup=build_operator_keyboard()
                )
                print(f"✅ Написал оператору @{username} ({tg_id})")
            except Exception as e:
                print(f"❌ Не смог написать оператору @{username} ({tg_id}): {e}")

    except Exception as e:
        print("❌ poll_operators ERROR:", e)


async def main():
    print("✅ report_scheduler.py запущен")
    print("Канал отчётов:", REPORT_CHAT_ID)

    while True:
        now = datetime.now(KYIV_TZ)
        due_slots = get_all_due_slots(now)

        for slot_id, slot_time, channel_id, slot_type, poll_slot_id in due_slots:
            last_sent = get_last_sent_slot_for(slot_id)
            if not last_sent:
                print("=" * 50)
                print(f"🔔 Слот: {slot_id} | Тип: {slot_type}")
                print("Канал:", channel_id)
                print("Сейчас:", now.strftime("%d.%m.%Y %H:%M:%S"))

                if slot_type == "poll":
                    await poll_operators(slot_id, channel_id)
                else:
                    await send_shift_report(slot_time, channel_id, poll_slot_id=poll_slot_id)

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
