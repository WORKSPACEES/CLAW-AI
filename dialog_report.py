def group_by_account(messages):
    accounts = defaultdict(list)

    for msg in messages:
        account_key = msg.get("account_session_name") or "unknown"
        accounts[str(account_key)].append(msg)

    return accounts


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
