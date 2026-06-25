from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from ai import analyze_dialog_with_groq

from supabase_db import supabase

KYIV_TZ = ZoneInfo("Europe/Kyiv")


def parse_dt(value):
    if isinstance(value, datetime):
        return value

    if not value:
        return None

    value = str(value).replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_messages(start_time, end_time):
    from datetime import timezone
    start_iso = start_time.astimezone(timezone.utc).isoformat()
    end_iso = end_time.astimezone(timezone.utc).isoformat()

    result = (
        supabase.table("telegram_messages")
        .select("*")
        .gte("message_date", start_iso)
        .lt("message_date", end_iso)
        .order("message_date", desc=False)
        .execute()
    )

    return result.data or []


def load_account_by_session(session_name):
    if not session_name:
        return {}

    result = (
        supabase.table("telegram_accounts")
        .select("*")
        .eq("session_name", session_name)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]

    return {}


def group_by_account(messages):
    accounts = defaultdict(list)

    for msg in messages:
        account_key = msg.get("account_session_name") or "unknown"
        accounts[str(account_key)].append(msg)

    return accounts


def group_by_dialog(messages):
    dialogs = defaultdict(list)

    for msg in messages:
        dialog_key = (
            msg.get("dialog_username")
            or msg.get("dialog_id")
            or msg.get("chat_id")
            or "unknown"
        )

        dialogs[str(dialog_key)].append(msg)

    return dialogs


def is_deleted_dialog(dialog_messages):
    for msg in dialog_messages:
        if msg.get("chat_deleted") is True:
            return True

    return False


def analyze_dialog(username, dialog_messages):
    incoming = [
        msg for msg in dialog_messages
        if msg.get("direction") == "incoming"
    ]

    outgoing = [
        msg for msg in dialog_messages
        if msg.get("direction") == "outgoing"
    ]

    deleted = is_deleted_dialog(dialog_messages)

    all_texts = [
        (msg.get("text") or "").lower()
        for msg in dialog_messages
        if msg.get("text")
    ]

    last_msg = dialog_messages[-1] if dialog_messages else {}
    last_text = last_msg.get("text") or "-"
    last_direction = last_msg.get("direction")

    joined_text = " ".join(all_texts)

    price_words = [
        "цена", "сколько", "стоимость", "прайс", "$", "доллар",
        "оплата", "заплатить", "скок"
    ]

    time_words = [
        "когда", "во сколько", "сегодня", "завтра", "час",
        "время", "сейчас"
    ]

    interest_words = [
        "интересно", "хочу", "можно", "давай", "подходит",
        "ок", "супер", "норм", "подойдет"
    ]

    reject_words = [
        "дорого", "не интересно", "не подходит", "подумаю",
        "потом", "нет", "не хочу"
    ]

    asked_price = any(word in joined_text for word in price_words)
    asked_time = any(word in joined_text for word in time_words)
    has_interest = any(word in joined_text for word in interest_words)
    has_reject = any(word in joined_text for word in reject_words)

    if deleted:
        diagnosis = "Удалил чат"
        detail = f"Чат стал недоступен. Последнее сообщение: {last_text[:120]}"
        result = "Потерян"
        manager_action = "Не требуется"

    elif incoming and not outgoing:
        diagnosis = "Не ответили"
        detail = f"Пользователь написал, но ответа не было. Последнее: {last_text[:120]}"
        result = "Нужен ответ"
        manager_action = "Срочно ответить"

    elif outgoing and not incoming:
        diagnosis = "Только исходящие"
        detail = f"Менеджер написал первым, ответа пока нет. Последнее: {last_text[:120]}"
        result = "Ждём ответа"
        manager_action = "Подождать / написать позже"

    elif last_direction == "incoming":
        diagnosis = "Клиент ждёт ответ"
        detail = f"Последнее сообщение от клиента: {last_text[:120]}"
        result = "Нужен ответ"
        manager_action = "Ответить"

    elif has_reject:
        diagnosis = "Сомневается / отказ"
        detail = f"В диалоге есть сомнение или отказ. Последнее: {last_text[:120]}"
        result = "Под вопросом"
        manager_action = "Дожать мягко / уточнить причину"

    elif asked_price and asked_time:
        diagnosis = "Горячий интерес"
        detail = f"Спрашивал цену и время. Последнее: {last_text[:120]}"
        result = "Хороший лид"
        manager_action = "Довести до встречи"

    elif asked_price:
        diagnosis = "Интерес по цене"
        detail = f"Пользователь спрашивал цену/условия. Последнее: {last_text[:120]}"
        result = "Есть интерес"
        manager_action = "Уточнить и закрыть на действие"

    elif asked_time:
        diagnosis = "Интерес по времени"
        detail = f"Пользователь спрашивал по времени. Последнее: {last_text[:120]}"
        result = "Есть интерес"
        manager_action = "Предложить конкретное время"

    elif has_interest:
        diagnosis = "Заинтересован"
        detail = f"В диалоге есть позитивный интерес. Последнее: {last_text[:120]}"
        result = "Перспективный"
        manager_action = "Продолжить диалог"

    else:
        diagnosis = "Обычный диалог"
        detail = f"Входящих: {len(incoming)}, исходящих: {len(outgoing)}. Последнее: {last_text[:120]}"
        result = "В работе"
        manager_action = "Проверить вручную"

    ai_result = analyze_dialog_with_groq(dialog_messages)

    return {
        "username": username,
        "deleted": deleted,
        "diagnosis": ai_result.get("diagnosis", "Неизвестно"),
        "detail": ai_result.get("detail", "Нет данных"),
        "result": ai_result.get("result", "Неизвестно"),
        "manager_action": ai_result.get("manager_action", "Проверить вручную"),
    }


def build_account_report_text(account_session_name, messages, start_time, end_time, shift_name, detailed=False):
    dialogs = group_by_dialog(messages)
    results = []

    account_username = "-"
    if messages:
        account_username = messages[0].get("account_username") or "-"

    account_data = load_account_by_session(account_session_name)

    ad_name = account_data.get("ad_name") or "-"
    operator_name = account_data.get("operator_name") or account_data.get("pc_name") or "-"
    phone = account_data.get("phone") or "-"

    for username, dialog_messages in dialogs.items():
        # Пропускаем системные чаты Telegram
        dialog_id = str(dialog_messages[0].get("dialog_id") or "")
        if dialog_id in ("777000", "42777", "0"):
            continue

        # Пропускаем ботов
        uname = str(username).lower()
        if uname.endswith("bot") or uname == "unknown":
            continue

        # Пропускаем диалоги без входящих сообщений — только исходящие не считаем лидами
        has_incoming = any(m.get("direction") == "incoming" for m in dialog_messages)
        if not has_incoming:
            continue

        # Пропускаем пустые диалоги
        has_text = any(m.get("text", "").strip() for m in dialog_messages)
        if not has_text:
            continue

        result = analyze_dialog(username, dialog_messages)
        results.append(result)

    total_written = len(results)
    deleted_chats = sum(1 for r in results if r.get("deleted") is True)
    remaining = max(0, total_written - deleted_chats)

    report_date = end_time.astimezone(KYIV_TZ).strftime("%d.%m")

    report = []
    report.append(f"Дата: {report_date}")
    report.append("День" if "Дневная" in shift_name else "Ночь")
    report.append(f"Реклама: {ad_name}")
    report.append(f"ПК: {operator_name}")
    report.append(f"Юзер: @{account_username}")
    report.append(f"Номер: {phone}")
    report.append("")
    report.append(f"Написало: {total_written}")
    report.append(f"Удалили чат: {deleted_chats}")
    report.append(f"Осталось: {remaining}")

    if not detailed:
        return "\n".join(report)

    report.append("")

    if not results:
        report.append("За этот период новых диалогов нет.")
        return "\n".join(report)

    for i, r in enumerate(results, start=1):
        username = str(r.get("username") or "unknown")

        if r.get("deleted") is True or username.isdigit():
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


def build_reports_by_accounts(start_time=None, end_time=None, shift_name=None, detailed=False):
    if start_time is None or end_time is None:
        period = get_current_shift_period()
        start_time = period["start_time"]
        end_time = period["end_time"]
        shift_name = period["shift_name"]

    messages = load_messages(start_time=start_time, end_time=end_time)
    accounts = group_by_account(messages)

    reports = []

    for account_session_name, account_messages in accounts.items():
        if not account_session_name or account_session_name == "unknown":
            continue

        text = build_account_report_text(
            account_session_name=account_session_name,
            messages=account_messages,
            start_time=start_time,
            end_time=end_time,
            shift_name=shift_name,
            detailed=detailed,
        )

        reports.append({
            "session_name": account_session_name,
            "text": text,
        })

    return reports


def build_report(start_time=None, end_time=None, shift_name=None, detailed=True):
    reports = build_reports_by_accounts(
        start_time=start_time,
        end_time=end_time,
        shift_name=shift_name,
        detailed=detailed,
    )

    if not reports:
        return "За этот период новых диалогов нет."

    return "\n\n".join(report["text"] for report in reports)
