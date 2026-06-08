import os
import json
import socket
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from groq import Groq
from supabase import create_client


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
ACCOUNT_META_FILE = BASE_DIR / "account_meta.json"

KYIV_TZ = ZoneInfo("Europe/Kyiv")


def load_env_manual(path):
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def load_account_meta():
    if not ACCOUNT_META_FILE.exists():
        return {}

    try:
        return json.loads(ACCOUNT_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


if ENV_PATH.exists():
    load_env_manual(ENV_PATH)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OWNER_ID = os.getenv("REPORT_CHAT_ID", "default_owner")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
groq = Groq(api_key=GROQ_API_KEY)


def to_iso(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KYIV_TZ)

    return dt.astimezone(ZoneInfo("UTC")).isoformat()


def get_current_shift_period(now=None):
    if now is None:
        now = datetime.now(KYIV_TZ)

    day_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    night_start = now.replace(hour=21, minute=0, second=0, microsecond=0)

    if day_start <= now < night_start:
        return {
            "shift_name": "Дневная смена",
            "start_time": day_start,
            "end_time": night_start,
        }

    if now >= night_start:
        return {
            "shift_name": "Ночная смена",
            "start_time": night_start,
            "end_time": day_start + timedelta(days=1),
        }

    yesterday_night = night_start - timedelta(days=1)

    return {
        "shift_name": "Ночная смена",
        "start_time": yesterday_night,
        "end_time": day_start,
    }


def get_last_finished_shift_period(now=None):
    if now is None:
        now = datetime.now(KYIV_TZ)

    today_9 = now.replace(hour=9, minute=0, second=0, microsecond=0)
    today_21 = now.replace(hour=21, minute=0, second=0, microsecond=0)

    if now >= today_21:
        return {
            "shift_name": "Дневная смена",
            "start_time": today_9,
            "end_time": today_21,
        }

    if now >= today_9:
        yesterday_21 = today_21 - timedelta(days=1)
        return {
            "shift_name": "Ночная смена",
            "start_time": yesterday_21,
            "end_time": today_9,
        }

    yesterday_9 = today_9 - timedelta(days=1)
    yesterday_21 = today_21 - timedelta(days=1)

    return {
        "shift_name": "Дневная смена",
        "start_time": yesterday_9,
        "end_time": yesterday_21,
    }


def load_messages(start_time=None, end_time=None, limit=2000):
    query = (
        supabase.table("telegram_messages")
        .select("*")
        .eq("owner_id", OWNER_ID)
        .order("message_date", desc=False)
        .limit(limit)
    )

    if start_time:
        query = query.gte("message_date", to_iso(start_time))

    if end_time:
        query = query.lt("message_date", to_iso(end_time))

    result = query.execute()
    return result.data or []


def load_account_by_session(account_session_name):
    if not account_session_name or account_session_name == "-":
        return {}

    try:
        result = (
            supabase.table("telegram_accounts")
            .select("*")
            .eq("session_name", account_session_name)
            .limit(1)
            .execute()
        )

        if result.data:
            return result.data[0]

    except Exception as e:
        print("ACCOUNT LOAD ERROR:", e)

    return {}


def group_by_dialog(messages):
    dialogs = defaultdict(list)

    for msg in messages:
        dialog_username = msg.get("dialog_username") or msg.get("dialog_id") or "unknown"
        dialogs[str(dialog_username)].append(msg)

    return dialogs


def is_deleted_chat(username):
    username = str(username or "").strip().lower()

    if not username:
        return True

    if username == "unknown":
        return True

    if "deleted" in username:
        return True

    if username.isdigit():
        return True

    return False


def analyze_dialog(username, messages):
    lines = []

    for msg in messages:
        direction = msg.get("direction")
        text = msg.get("text", "")
        role = "КЛИЕНТ" if direction == "incoming" else "МЫ"
        lines.append(f"{role}: {text}")

    dialog_text = "\n".join(lines[-100:])

    prompt = f"""
Ты анализируешь переписку менеджера с человеком в Telegram.

Не пиши красивые статусы типа "горячий лид".
Нужно кратко объяснить, что произошло с этим лидом.

Верни строго JSON без markdown:

{{
  "username": "{username}",
  "diagnosis": "",
  "detail": "",
  "result": "",
  "manager_action": ""
}}

Правила diagnosis:
- "не сошлись по сумме"
- "хотел дешевле"
- "хотел бесплатно"
- "не хочет платить такси"
- "согласился по сумме"
- "пропал после цены"
- "диалог в работе"
- "непонятно, нужен ручной просмотр"

Пример:
КЛИЕНТ: Даю 15
МЫ: Хочу 35

Ответ:
{{
  "username": "{username}",
  "diagnosis": "не сошлись по сумме",
  "detail": "клиент хотел 15, ему ответили 35",
  "result": "отказ",
  "manager_action": "можно попробовать предложить промежуточную сумму"
}}

Диалог:
{dialog_text}
"""

    response = groq.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )

    text = response.choices[0].message.content.strip()

    try:
        return json.loads(text)
    except Exception:
        return {
            "username": username,
            "diagnosis": "непонятно, нужен ручной просмотр",
            "detail": text,
            "result": "ручной просмотр",
            "manager_action": "проверить диалог вручную",
        }


def format_dt(dt):
    return dt.astimezone(KYIV_TZ).strftime("%d.%m %H:%M")


def build_report(start_time=None, end_time=None, shift_name=None):
    if start_time is None or end_time is None:
        period = get_current_shift_period()
        start_time = period["start_time"]
        end_time = period["end_time"]
        shift_name = period["shift_name"]

    messages = load_messages(start_time=start_time, end_time=end_time)
    dialogs = group_by_dialog(messages)

    results = []

    account_username = "-"
    account_session_name = "-"

    if messages:
        account_username = messages[0].get("account_username") or "-"
        account_session_name = messages[0].get("account_session_name") or "-"

    pc_name = socket.gethostname()

    account_data = load_account_by_session(account_session_name)

    ad_name = account_data.get("ad_name") or "-"
    operator_name = (
        account_data.get("operator_name")
        or account_data.get("pc_name")
        or pc_name
    )
    phone = account_data.get("phone") or "-"

    for username, dialog_messages in dialogs.items():
        result = analyze_dialog(username, dialog_messages)
        results.append(result)

    total_written = len(results)

    deleted_dialogs = set()

    for msg in messages:
        if msg.get("chat_deleted") is True:
            dialog_key = msg.get("dialog_username") or msg.get("dialog_id")
            if dialog_key:
                deleted_dialogs.add(str(dialog_key))

    deleted_chats = len(deleted_dialogs)

    remaining = total_written - deleted_chats

    report_date = end_time.astimezone(KYIV_TZ).strftime("%d.%m.%Y")

    report = []
    report.append("📊 Отчёт по Telegram")
    report.append("")
    report.append(f"Дата отчёта: {report_date}")
    report.append(f"Смена: {shift_name}")
    report.append(f"Период: {format_dt(start_time)} — {format_dt(end_time)}")
    report.append("")
    report.append(f"Реклама: {ad_name}")
    report.append(f"Оператор / ПК: {operator_name}")
    report.append(f"Юзер: @{account_username}")
    report.append(f"Телефон: {phone}")
    report.append("")
    report.append(f"Всего написали: {total_written}")
    report.append(f"Удалили чат: {deleted_chats}")
    report.append(f"Осталось: {remaining}")
    report.append("")

    if not results:
        report.append("За этот период новых диалогов нет.")
        return "\n".join(report)

    for i, r in enumerate(results, start=1):
        username = r.get("username") or "unknown"

        if is_deleted_chat(username):
            username_line = f"ID {username}"
        else:
            username_line = f"@{username}"

        report.append(f"{i}. {username_line}")
        report.append(f"Диагноз: {r.get('diagnosis')}")
        report.append(f"Деталь: {r.get('detail')}")
        report.append(f"Итог: {r.get('result')}")
        report.append(f"Действие: {r.get('manager_action')}")
        report.append("")

    return "\n".join(report)


def build_last_finished_shift_report():
    period = get_last_finished_shift_period()

    return build_report(
        start_time=period["start_time"],
        end_time=period["end_time"],
        shift_name=period["shift_name"],
    )


if __name__ == "__main__":
    print(build_last_finished_shift_report())
