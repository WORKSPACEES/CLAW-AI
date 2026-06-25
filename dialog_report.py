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
    start_iso = start_time.astimezone(KYIV_TZ).isoformat()
    end_iso = end_time.astimezone(KYIV_TZ).isoformat()

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
    account_data = load_account_by_session(account_session_name)

    start_iso = start_time.astimezone(KYIV_TZ).isoformat()
    end_iso = end_time.astimezone(KYIV_TZ).isoformat()

    # Берём ВСЕ сообщения аккаунта за смену для развёрнутого анализа Groq
    all_res = (
        supabase.table("telegram_messages")
        .select("*")
        .eq("account_session_name", account_session_name)
        .gte("message_date", start_iso)
        .lt("message_date", end_iso)
        .order("message_date", desc=False)
        .execute()
    )

    all_messages = all_res.data or []

    # Отдельно берём только входящие — это лиды
    incoming_messages = [
        msg for msg in all_messages
        if msg.get("direction") == "incoming"
    ]

    def first_value(*keys, default="-"):
        for key in keys:
            for msg in incoming_messages:
                value = msg.get(key)
                if value not in (None, "", "NULL"):
                    return value

        for key in keys:
            for msg in all_messages:
                value = msg.get(key)
                if value not in (None, "", "NULL"):
                    return value

        for key in keys:
            value = account_data.get(key)
            if value not in (None, "", "NULL"):
                return value

        return default

    account_username = first_value("account_username", "username")
    ad_name = first_value("ad_name")
    operator_name = first_value("operator_name", "pc_name")
    phone = first_value("phone")

    def make_dialog_key(msg):
        dialog_id = str(msg.get("dialog_id") or msg.get("chat_id") or "")
        dialog_username = msg.get("dialog_username") or msg.get("username")
        dialog_name = msg.get("dialog_name") or msg.get("chat_name") or msg.get("sender_name")

        return (
            dialog_id
            or dialog_username
            or dialog_name
            or str(msg.get("id"))
        )

    # Группируем ВСЕ сообщения по диалогам, чтобы Groq видел весь диалог, а не только входящие
    dialog_messages_map = defaultdict(list)

    for msg in all_messages:
        dialog_key = make_dialog_key(msg)
        if dialog_key:
            dialog_messages_map[str(dialog_key)].append(msg)

    all_dialogs = set()
    deleted_dialogs = set()
    active_dialogs = set()

    leads = {}

    for msg in incoming_messages:
        dialog_id = str(msg.get("dialog_id") or msg.get("chat_id") or "")
        dialog_username = msg.get("dialog_username") or msg.get("username")
        dialog_name = msg.get("dialog_name") or msg.get("chat_name") or msg.get("sender_name")

        # Пропускаем системные чаты Telegram
        if dialog_id in ("777000", "42777", "0"):
            continue

        # Пропускаем ботов
        uname = str(dialog_username or "").lower()
        if uname.endswith("bot"):
            continue

        dialog_key = make_dialog_key(msg)
        if not dialog_key:
            continue

        dialog_key = str(dialog_key)

        all_dialogs.add(dialog_key)

        if msg.get("chat_deleted") is True:
            deleted_dialogs.add(dialog_key)
        else:
            active_dialogs.add(dialog_key)

        msg_date = parse_dt(msg.get("message_date")) or parse_dt(msg.get("created_at"))

        if dialog_key not in leads:
            leads[dialog_key] = {
                "dialog_id": dialog_id,
                "username": dialog_username,
                "name": dialog_name,
                "last_text": msg.get("text") or "",
                "last_date": msg_date,
            }
        else:
            old_date = leads[dialog_key].get("last_date")
            if old_date is None or (msg_date is not None and msg_date >= old_date):
                leads[dialog_key]["last_text"] = msg.get("text") or ""
                leads[dialog_key]["last_date"] = msg_date
                leads[dialog_key]["username"] = dialog_username or leads[dialog_key].get("username")
                leads[dialog_key]["name"] = dialog_name or leads[dialog_key].get("name")

    total_written = len(all_dialogs)
    deleted_chats = len(deleted_dialogs)
    remaining = len(active_dialogs)

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

    # Обычный отчёт — только цифры, без Groq
    if not detailed:
        return "\n".join(report)

    report.append("")

    if not leads:
        report.append("За этот период новых диалогов нет.")
        return "\n".join(report)

    # Развёрнутый отчёт — тут уже анализируем Groq
    for i, (dialog_key, lead) in enumerate(leads.items(), start=1):
        username = lead.get("username")
        dialog_id = lead.get("dialog_id")
        name = lead.get("name") or "-"

        if username:
            user_line = f"@{username}"
            analyze_name = username
        elif dialog_id:
            user_line = f"ID {dialog_id}"
            analyze_name = dialog_id
        else:
            user_line = name
            analyze_name = name

        dialog_messages = dialog_messages_map.get(dialog_key) or []

        try:
            ai_result = analyze_dialog(analyze_name, dialog_messages)
        except Exception as e:
            print("❌ GROQ ANALYZE ERROR:", e)
            ai_result = {
                "diagnosis": "Ошибка анализа",
                "detail": "Groq не смог обработать диалог",
                "result": "Проверить вручную",
                "manager_action": "Открыть чат и проверить",
            }

        report.append(f"{i}. {user_line}")
        report.append(f"Имя: {name}")
        report.append(f"Удалил чат: {'Да' if dialog_key in deleted_dialogs else 'Нет'}")
        report.append(f"Последнее сообщение: {lead.get('last_text') or '-'}")
        report.append(f"Диагноз: {ai_result.get('diagnosis')}")
        report.append(f"Деталь: {ai_result.get('detail')}")
        report.append(f"Итог: {ai_result.get('result')}")
        report.append(f"Действие: {ai_result.get('manager_action')}")
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
