import asyncio
import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter
from dotenv import load_dotenv
from aiogram.types import FSInputFile
from aiogram.types import WebAppInfo
from telegram_connect import (
    start_login,
    confirm_code,
    confirm_2fa,
    list_accounts,
    delete_account_by_phone,
    start_qr_login,
    wait_qr_login,
)
from dialog_report import build_report, build_reports_by_accounts
from pathlib import Path
import json
import re

from database import get_today_stats, get_today_messages, get_messages_by_chat_query
from ai import analyze_messages_with_groq, chat_with_groq
from command_memory import detect_command_by_memory, auto_learn_from_previous
from image_ai import build_image_url
from supabase_db import (
    save_bot_channels,
    get_bot_channels,
    link_account_to_channel,
    get_channel_for_account,
    get_all_account_channels,
    remove_bot_channel,
    get_timer_settings,
    set_timer_settings,
)
load_dotenv()

# ─── operator_states — состояние опроса операторов ───────────────────────────
operator_poll_state = {}  # {telegram_id: {"step": ..., "zahody": 0, "broni": 0, "razvoroty": 0, "slot": ...}}

class TimerSetup(StatesGroup):
    choosing_channel = State()
    waiting_day_time = State()
    waiting_night_time = State()
    confirming = State()


def parse_time(text: str):
    """
    Парсит время из строки. Принимает форматы: 21:00, 21-00, 21.00, 21
    Возвращает (hour, minute) или None если не распознал.
    """
    text = text.strip()
    match = re.match(r"^(\d{1,2})[:.\-](\d{2})$", text)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    match = re.match(r"^(\d{1,2})$", text)
    if match:
        h = int(match.group(1))
        if 0 <= h <= 23:
            return h, 0
    return None


def build_timer_channel_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = []
    for ch in channels:
        title = ch.get("channel_title") or ch.get("channel_id")
        cid = ch.get("channel_id")
        buttons.append([
            InlineKeyboardButton(
                text=f"📢 {title}",
                callback_data=f"timer_pick_channel:{cid}:{title[:30]}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Сохранить", callback_data="timer_confirm:yes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="timer_confirm:no"),
        ]
    ])

last_unknown_text = {}
login_state = {}

ACCOUNT_META_FILE = Path("account_meta.json")


def load_account_meta():
    if not ACCOUNT_META_FILE.exists():
        return {}
    try:
        return json.loads(ACCOUNT_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_account_meta(data):
    ACCOUNT_META_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

BOT_TOKEN = os.getenv("BOT_TOKEN")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в .env")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def build_report_text(with_ai=False):
    stats = get_today_stats()

    triggers_text = ""
    if stats["trigger_counts"]:
        for word, count in stats["trigger_counts"].items():
            triggers_text += f"- {word}: {count}\n"
    else:
        triggers_text = "Пока триггеров нет"

    report_text = (
        "📊 Отчёт за сегодня\n\n"
        f"💬 Сообщений: {stats['total_messages']}\n"
        f"👥 Уникальных людей: {stats['unique_people']}\n\n"
        f"🔥 Триггеры:\n{triggers_text}"
    )

    if with_ai:
        try:
            messages = get_today_messages(25)
            ai_text = analyze_messages_with_groq(messages)
            report_text += f"\n\n🤖 AI-анализ:\n{ai_text}"
        except Exception as e:
            print("❌ GROQ REPORT ERROR:", e)
            report_text += "\n\n🤖 AI-анализ временно недоступен: лимит Groq. Попробуй через 10–20 секунд."

    return report_text


@dp.message(CommandStart())
async def start(message: types.Message):
    await message.answer(
        "✅ ANALIZATOR запущен.\n\n"
        "Можешь писать мне обычным текстом:\n"
        "— привет\n"
        "— сколько дней в июне\n"
        "— скинь отчет сейчас\n"
        "— скинь отчет в канал\n"
        "— покажи лидов"
    )


@dp.message(Command("analytics"))
async def analytics(message: types.Message):
    await message.answer(build_report_text(with_ai=False))


@dp.message(Command("report"))
async def send_report(message: types.Message):
    if not REPORT_CHAT_ID:
        await message.answer("❌ REPORT_CHAT_ID не указан в .env")
        return

    reports = build_reports_by_accounts(detailed=False)

    if not reports:
        await bot.send_message(REPORT_CHAT_ID, "За этот период новых диалогов нет.")
        await message.answer("✅ Отчёт отправлен в канал")
        return

    for report in reports:
        await bot.send_message(
            REPORT_CHAT_ID,
            report["text"],
            reply_markup=report_keyboard(report["session_name"])
        )

    await message.answer("✅ Отчёт отправлен в канал")


@dp.message(Command("ai_report"))
async def ai_report(message: types.Message):
    await message.answer("🤖 Анализирую сообщения через Groq...")

    try:
        ai_text = analyze_messages_with_groq(get_today_messages(25))
        await message.answer("🤖 AI-анализ за сегодня:\n\n" + ai_text)
    except Exception as e:
        print("❌ GROQ AI_REPORT ERROR:", e)
        await message.answer("Groq сейчас упёрся в лимит. Попробуй через 10–20 секунд.")


@dp.message(Command("full_report"))
async def full_report(message: types.Message):
    if not REPORT_CHAT_ID:
        await message.answer("❌ REPORT_CHAT_ID не указан в .env")
        return

    await message.answer("🤖 Собираю полный отчёт...")
    await bot.send_message(REPORT_CHAT_ID, build_report_text(with_ai=True))
    await message.answer("✅ Полный отчёт отправлен в канал")


def is_image_request(text):
    text = text.lower()

    return (
        "сгенерируй" in text
        or "создай" in text
        or "нарисуй" in text
    )


def extract_image_prompt(text):
    prompt = text.lower()

    words_to_remove = [
        "сгенерируй",
        "создай",
        "нарисуй",
        "мне",
        "фото",
        "картинку",
        "изображение",
        "пожалуйста",
        "пж"
    ]

    for word in words_to_remove:
        prompt = prompt.replace(word, "")

    prompt = prompt.strip()

    if not prompt:
        prompt = "красивый цветок"

    return prompt


def extract_report_chat_query(text):
    lower = text.lower()

    markers = [
        "отчет по",
        "отчёт по",
        "отчет кинь мне по",
        "отчёт кинь мне по",
        "скинь отчет по",
        "скинь отчёт по",
    ]

    for marker in markers:
        if marker in lower:
            query = text[lower.find(marker) + len(marker):].strip()
            query = query.replace("@", "").strip()
            return query

    return None

def report_keyboard(session_name):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📖 Развёрнутый отчёт",
                    callback_data=f"full_report_account:{session_name}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=f"delete_report_msg:{session_name}"
                )
            ]
        ]
    )

async def refresh_bot_channels(owner_user_id: int) -> list:
    """Читает из Supabase список каналов/групп где бот является админом."""
    try:
        return await asyncio.to_thread(
            get_bot_channels,
            str(owner_user_id)
        )
    except Exception as e:
        print("❌ refresh_bot_channels ERROR:", e)
        return []


def build_channel_keyboard(channels: list) -> InlineKeyboardMarkup:
    """Строит клавиатуру с кнопками выбора канала/группы."""
    buttons = []
    for ch in channels:
        title = ch.get("channel_title") or ch.get("channel_id")
        cid = ch.get("channel_id")
        buttons.append([
            InlineKeyboardButton(
                text=f"📢 {title}",
                callback_data=f"pick_channel:{cid}:{title[:30]}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="⏭ Пропустить (без канала)",
            callback_data="pick_channel:skip:Без канала"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_report_channel_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = []
    for ch in channels:
        title = ch.get("channel_title") or ch.get("channel_id")
        cid = ch.get("channel_id")
        buttons.append([
            InlineKeyboardButton(
                text=f"📢 {title}",
                callback_data=f"report_to_channel:{cid}:{title[:30]}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def login_code_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔳 Войти по QR",
                    callback_data="login_by_qr"
                )
            ]
        ]
    )

WEBAPP_BASE_URL = (os.getenv("WEBAPP_BASE_URL") or "").rstrip("/")


def twofa_keyboard(token):
    if not WEBAPP_BASE_URL or not token:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔐 Ввести 2FA",
                    web_app=WebAppInfo(
                        url=f"{WEBAPP_BASE_URL}/twofa?token={token}"
                    )
                )
            ]
        ]
    )

@dp.callback_query(lambda c: c.data.startswith("full_report_account:"))
async def full_report_callback(callback: types.CallbackQuery):
    try:
        session_name = callback.data.split(":", 1)[1]

        await callback.answer("📩 Отправляю развёрнутый отчёт в личку")
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"🤖 Анализирую диалоги для @{session_name}..."
        )

        reports = build_reports_by_accounts(
            detailed=True,
            session_name_filter=session_name
        )

        needed_report = None
        for report in reports:
            if report["session_name"] == session_name:
                needed_report = report
                break

        if not needed_report:
            await bot.send_message(
                chat_id=callback.from_user.id,
                text="За этот период по этому аккаунту нет данных."
            )
            return

        text = needed_report["text"]
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await bot.send_message(
                chat_id=callback.from_user.id,
                text=chunk
            )

    except Exception as e:
        print("FULL REPORT CALLBACK ERROR:", e)
        await callback.answer("Ошибка развёрнутого отчёта", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith("delete_report_msg:"))
async def delete_report_msg_callback(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
    except Exception as e:
        print("DELETE MSG ERROR:", e)
        await callback.answer("Не удалось удалить", show_alert=True)

@dp.callback_query(lambda c: c.data.startswith("pick_channel:"))
async def pick_channel_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    state = login_state.get(user_id)

    if not state:
        await callback.answer("Сессия устарела. Начни заново: подключить тг", show_alert=True)
        return

    parts = callback.data.split(":", 2)
    channel_id = parts[1] if len(parts) > 1 else "skip"
    channel_title = parts[2] if len(parts) > 2 else "Без канала"

    if channel_id == "skip":
        state["report_channel_id"] = None
        state["report_channel_title"] = None
    else:
        state["report_channel_id"] = channel_id
        state["report_channel_title"] = channel_title

    state["step"] = "waiting_ad_name"

    await callback.answer()
    await bot.send_message(
        chat_id=user_id,
        text=f"✅ Канал выбран: {channel_title}\n\nКакая реклама?"
    )

@dp.callback_query(lambda c: c.data.startswith("report_to_channel:"))
async def report_to_channel_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":", 2)
    channel_id = parts[1] if len(parts) > 1 else None
    channel_title = parts[2] if len(parts) > 2 else "Канал"

    await callback.answer()
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=f"📊 Собираю отчёт для «{channel_title}»..."
    )

    try:
        all_channels = await asyncio.to_thread(get_all_account_channels)

        # Берём только session_name привязанные к выбранному каналу
        session_names = [
            row["session_name"]
            for row in all_channels
            if str(row["channel_id"]) == str(channel_id)
        ]

        reports = build_reports_by_accounts(detailed=False)

        filtered = [
            r for r in reports
            if r["session_name"] in session_names
        ]

        if not filtered:
            await bot.send_message(
                chat_id=callback.from_user.id,
                text="За этот период новых диалогов нет по этому каналу."
            )
            return

        for report in filtered:
            await bot.send_message(
                channel_id,
                report["text"],
                reply_markup=report_keyboard(report["session_name"])
            )

        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"✅ Отчёт отправлен в «{channel_title}»"
        )

    except Exception as e:
        print("REPORT TO CHANNEL CALLBACK ERROR:", e)
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"❌ Ошибка отчёта: {e}"
        )

@dp.callback_query(lambda c: c.data == "login_by_qr")
async def login_by_qr_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    state = login_state.get(user_id)

    if not state:
        await callback.answer("Сначала напиши: подключить тг", show_alert=True)
        return

    await callback.answer("Генерирую QR...")

    await bot.send_message(
        chat_id=user_id,
        text="🔳 Генерирую QR-код для входа..."
    )

    result = await start_qr_login(
        user_id,
        ad_name=state.get("ad_name"),
        pc_name=state.get("pc_name"),
        operator_name=state.get("pc_name"),
    )

    if not result.get("ok"):
        await bot.send_message(
            chat_id=user_id,
            text=result["message"]
        )
        return

    qr_path = result.get("qr_path")

    await bot.send_photo(
        chat_id=user_id,
        photo=FSInputFile(qr_path),
        caption=(
            "🔳 Отсканируй этот QR через Telegram.\n\n"
            "Telegram → Настройки → Устройства → Подключить устройство.\n\n"
            "У тебя примерно 60–90 секунд."
        )
    )

    wait_result = await wait_qr_login(user_id, timeout=90)

    if wait_result.get("needs_2fa"):
        state["step"] = "waiting_2fa"
        state["twofa_token"] = wait_result.get("twofa_token")

        await bot.send_message(
            chat_id=user_id,
            text=wait_result["message"] + "\n\nНажми кнопку ниже и введи пароль 2FA.",
            reply_markup=twofa_keyboard(wait_result.get("twofa_token"))
        )
        return

    if wait_result.get("ok"):
        channel_id = state.get("report_channel_id")
        channel_title = state.get("report_channel_title") or "Без канала"

        if channel_id:
            accounts = list_accounts(user_id)
            session_name = None

            for acc in accounts:
                session_name = acc.get("session_name")
                break

            if session_name:
                await asyncio.to_thread(
                    link_account_to_channel,
                    str(user_id),
                    session_name,
                    channel_id,
                    channel_title,
                )

        if user_id in login_state:
            del login_state[user_id]

        channel_msg = f"\n📢 Отчёты будут в: {channel_title}" if channel_id else ""
        await bot.send_message(
            chat_id=user_id,
            text=wait_result["message"] + channel_msg
        )
        return

    await bot.send_message(
        chat_id=user_id,
        text=wait_result["message"]
    )
    return

@dp.message(lambda m: (m.text or "").strip().lower() == "установить таймер")
async def set_timer_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    channels = await refresh_bot_channels(user_id)

    if not channels:
        await message.answer(
            "⚠️ Нет каналов где я являюсь админом.\n"
            "Добавь меня как админа в нужный канал и попробуй снова."
        )
        return

    await state.set_state(TimerSetup.choosing_channel)
    await message.answer(
        "📢 Для какого канала установить расписание?",
        reply_markup=build_timer_channel_keyboard(channels)
    )

@dp.message(StateFilter(None))
async def admin_chat(message: types.Message):
    # Игнорируем сообщения из каналов и групп — только личка
    # Для групп — только команда Claw
    if message.chat.type in ("group", "supergroup"):
        text_check = (message.text or "").strip().lower()
        if text_check == "claw":
            try:
                channel_id = str(message.chat.id)
                channel_title = message.chat.title or channel_id
                await asyncio.to_thread(
                    save_bot_channels,
                    "default",
                    [{
                        "channel_id": channel_id,
                        "channel_title": channel_title,
                    }]
                )
                await message.answer(
                    f"✅ Группа «{channel_title}» подключена.\n"
                    "Теперь можно привязывать Telegram-аккаунты."
                )
                print(f"✅ Группа сохранена: {channel_title} ({channel_id})")
            except Exception as e:
                print("❌ GROUP SAVE ERROR:", e)
        return

    # Каналы и остальное — игнорируем
    if message.chat.type != "private":
        return

    text = message.text or ""

    if text.startswith("/"):
        return

    user_id = message.from_user.id
    lower_text = text.lower().strip()

    # 0.0 Загрузка истории
    if lower_text in ("загрузи историю", "загрузить историю", "прочитай чаты", "читай историю"):
        await load_history_command(message)
        return

    # 0. Удаление Telegram-сессии по номеру
    if (
        "удали сессию" in lower_text
        or "удалить сессию" in lower_text
        or "удали эту сессию" in lower_text
        or "удалить эту сессию" in lower_text
        or "удали аккаунт" in lower_text
        or "удалить аккаунт" in lower_text
        or "отключи тг" in lower_text
        or "отключить тг" in lower_text
        or "удали тг" in lower_text
        or "удалить тг" in lower_text
    ):
        phone_match = re.search(r"\+?\d[\d\s\-\(\)]{7,25}\d", text)

        if not phone_match:
            await message.answer(
                "❌ Не вижу номер.\n\n"
                "Напиши:\n"
                "удали сессию +91 98751 68274"
            )
            return

        phone = phone_match.group(0).strip()
        result = delete_account_by_phone(user_id, phone)

        await message.answer(result["message"])
        return

    # 1. Отчёт именно в канал
    if (
        "скинь отчет в канал" in lower_text
        or "скинь отчёт в канал" in lower_text
        or "отправь отчет в канал" in lower_text
        or "отправь отчёт в канал" in lower_text
        or "дай отчет в канал" in lower_text
        or "дай отчёт в канал" in lower_text
        or "опубликуй отчет" in lower_text
        or "опубликуй отчёт" in lower_text
        or "выложи отчет" in lower_text
        or "выложи отчёт" in lower_text
        or "отправь статистику в канал" in lower_text
        or "скинь статистику в канал" in lower_text
        or "кинь статистику в канал" in lower_text
        or "опубликуй статистику" in lower_text
        or "выложи статистику" in lower_text
        or "кинь сводку в канал" in lower_text
        or "отчет в канал" in lower_text
        or "в канал отчет" in lower_text
        or "отчет в канал кинь" in lower_text
    ):
        channels = await asyncio.to_thread(get_bot_channels, "default")

        if not channels:
            await message.answer("❌ Нет подключённых каналов. Напиши Claw в нужном канале/группе.")
            return

        await message.answer(
            "В какой канал отправить отчёт?",
            reply_markup=build_report_channel_keyboard(channels)
        )

        return

    # 2. Обычный отчёт в личку
    if (
        "скинь отчет" in lower_text
        or "скинь отчёт" in lower_text
        or "дай отчет" in lower_text
        or "дай отчёт" in lower_text
        or lower_text == "отчет"
        or lower_text == "отчёт"
        or "статистика" in lower_text
        or "дай статистику" in lower_text
        or "покажи статистику" in lower_text
        or "сводка" in lower_text
        or "дай сводку" in lower_text
        or "как дела по чатам" in lower_text
        or "что по чатам" in lower_text
        or "сколько написало" in lower_text
        or "сколько осталось" in lower_text
        or "анализ чатов" in lower_text
        or "отчет по чатам" in lower_text
        or "отчёт по чатам" in lower_text
    ):
        await message.answer("📊 Собираю отчёт...")

        try:
            reports = build_reports_by_accounts(detailed=False)

            if not reports:
                await message.answer("За этот период новых диалогов нет.")
                return

            for report in reports:
                await message.answer(
                    report["text"],
                    reply_markup=report_keyboard(report["session_name"])
                )

        except Exception as e:
            print("DIALOG REPORT ERROR:", e)
            await message.answer(f"❌ Ошибка отчёта: {e}")

        return

    # 3. Состояние подключения Telegram
    if user_id in login_state:
        state = login_state[user_id]
        step = state.get("step")

        if step == "waiting_ad_name":
            state["ad_name"] = text.strip()
            state["step"] = "waiting_pc_name"
            await message.answer("Окей. Какой ПК / оператор?")
            return

        if step == "waiting_pc_name":
            state["pc_name"] = text.strip()
            state["step"] = "waiting_phone"
            await message.answer("Теперь пришли номер Telegram в формате +380...")
            return

        if step == "waiting_phone":
            phone = text.strip()
            state["phone"] = phone

            await message.answer("📩 Отправляю код в Telegram...")

            try:
                result = await start_login(
                    user_id,
                    phone,
                    ad_name=state.get("ad_name"),
                    pc_name=state.get("pc_name"),
                    operator_name=state.get("pc_name"),
                )

                if not result.get("ok"):
                    if user_id in login_state:
                        del login_state[user_id]

                    await message.answer(result["message"])
                    return

                state["step"] = "waiting_code"

                await message.answer(
                    result["message"],
                    reply_markup=login_code_keyboard()
                )
                return

            except Exception as e:
                print("❌ START LOGIN ERROR:", e)

                if user_id in login_state:
                    del login_state[user_id]

                await message.answer(
                    f"❌ Ошибка отправки кода: {e}\n\n"
                    "Подключение сброшено. Напиши заново: подключить тг"
                )
                return

        if step == "waiting_code":
            code = text.strip().replace(" ", "")

            try:
                result = await confirm_code(user_id, code)

                if result.get("needs_2fa"):
                    state["step"] = "waiting_2fa"
                    state["twofa_token"] = result.get("twofa_token")

                    await message.answer(
                        result["message"] + "\n\nНажми кнопку ниже и введи пароль 2FA.",
                        reply_markup=twofa_keyboard(result.get("twofa_token"))
                    )
                    return

                if result.get("ok"):
                    meta = load_account_meta()

                    meta[str(user_id)] = {
                        "ad_name": state.get("ad_name"),
                        "pc_name": state.get("pc_name"),
                        "phone": state.get("phone"),
                    }

                    save_account_meta(meta)

                    # Привязываем аккаунт к выбранному каналу
                    channel_id = state.get("report_channel_id")
                    channel_title = state.get("report_channel_title") or "Без канала"

                    if channel_id:
                        accounts = list_accounts(user_id)
                        phone = state.get("phone", "")
                        session_name = None

                        for acc in accounts:
                            if acc.get("phone", "").replace("+", "") in phone.replace("+", ""):
                                session_name = acc.get("session_name")
                                break

                        if session_name:
                            await asyncio.to_thread(
                                link_account_to_channel,
                                str(user_id),
                                session_name,
                                channel_id,
                                channel_title,
                            )

                    if user_id in login_state:
                        del login_state[user_id]

                    channel_msg = f"\n📢 Отчёты будут в: {channel_title}" if channel_id else ""
                    await message.answer(result["message"] + channel_msg)
                    return

                if user_id in login_state:
                    del login_state[user_id]

                await message.answer(
                    result["message"]
                    + "\n\nЯ сбросил подключение. Напиши заново: подключить тг"
                )
                return

            except Exception as e:
                print("❌ CONFIRM CODE ERROR:", e)

                if user_id in login_state:
                    del login_state[user_id]

                await message.answer(
                    f"❌ Ошибка подтверждения кода: {e}\n\n"
                    "Подключение сброшено. Напиши заново: подключить тг"
                )
                return

        if step == "waiting_2fa":
            password = text.strip()

            await message.answer("🔐 Проверяю пароль 2FA...")

            result = await confirm_2fa(user_id, password)

            if result.get("ok"):
                if user_id in login_state:
                    del login_state[user_id]

                await message.answer(result["message"])
                return

            if result.get("wrong_password"):
                await message.answer(result["message"])
                return

            if user_id in login_state:
                del login_state[user_id]

            await message.answer(
                result["message"]
                + "\n\nЯ сбросил подключение. Напиши заново: подключить тг"
            )
            return

        if user_id in login_state:
            del login_state[user_id]

        await message.answer(
            "❌ Неизвестный шаг подключения. Я сбросил вход.\n\n"
            "Напиши заново: подключить тг"
        )
        return

    # 4. Подключение Telegram
    if (
        "подключить тг" in lower_text
        or "подключи тг" in lower_text
        or "подключим тг" in lower_text
        or "подключить телеграм" in lower_text
        or "подключи телеграм" in lower_text
        or "добавить аккаунт" in lower_text
        or "добавить тг" in lower_text
        or "добавить телеграм" in lower_text
        or "новый аккаунт" in lower_text
        or "подключить аккаунт" in lower_text
        or "подключить номер" in lower_text
        or "добавить номер" in lower_text
        or "авторизовать аккаунт" in lower_text
        or "давай тг подключим" in lower_text
        or "нужно тг подключить" in lower_text
        or "подключим тг давай" in lower_text
        or "хочу тг подключить" in lower_text
        or "тг подключим давай сейчас" in lower_text
        or "тг нужно подключить" in lower_text
    ):
        channels = await refresh_bot_channels(user_id)

        login_state[user_id] = {
            "step": "waiting_channel",
            "report_channel_id": None,
            "report_channel_title": None,
            "ad_name": None,
            "pc_name": None,
            "phone": None,
        }

        if channels:
            await message.answer(
                "Окей. В какой канал или группу закрепить этот Telegram?",
                reply_markup=build_channel_keyboard(channels)
            )
        else:
            state = login_state[user_id]
            state["step"] = "waiting_pc_name"
            await message.answer(
                "⚠️ Я пока не вижу каналов где я админ.\n\n"
                "Добавь меня как админа в нужный канал/группу, потом попробуй снова.\n\n"
                "Или продолжим без канала — какой ПК / оператор?"
            )
        return

    # 5. Список аккаунтов
    if (
        "мои тг" in lower_text
        or "список тг" in lower_text
        or "дай список тг" in lower_text
        or "покажи список тг" in lower_text
        or "сколько тг" in lower_text
        or "сколько телеграм" in lower_text
        or "какие тг" in lower_text
        or "какие телеграм" in lower_text
        or "какие telegram" in lower_text
        or "что подключено" in lower_text
        or "что у нас подключено" in lower_text
        or "сейчас подключено" in lower_text
        or "подключенные тг" in lower_text
        or "подключённые тг" in lower_text
        or "подключенные телеграм" in lower_text
        or "подключённые телеграм" in lower_text
        or "подключенные аккаунты" in lower_text
        or "подключённые аккаунты" in lower_text
        or "аккаунты тг" in lower_text
        or "telegram аккаунты" in lower_text
        or "тг аккаунты" in lower_text
        or "сколько тг есть" in lower_text
        or "сколько есть тг" in lower_text
        or "какие тг есть" in lower_text
    ):
        accounts = list_accounts(user_id)

        if not accounts:
            await message.answer("Пока нет подключенных Telegram-аккаунтов.")
            return

        text_accounts = f"📱 Подключенные Telegram: {len(accounts)}\n\n"

        for index, acc in enumerate(accounts, start=1):
            username = acc.get("username") or "без username"
            first_name = acc.get("first_name") or "Без имени"
            phone = acc.get("phone") or "номер скрыт"

            text_accounts += (
                f"{index}. {first_name} / @{username}\n"
                f"   Телефон: {phone}\n\n"
            )

        await message.answer(text_accounts)
        return

    report_query = extract_report_chat_query(text)

    if report_query:
        messages = get_messages_by_chat_query(report_query, 80)

        if not messages:
            await message.answer(
                f"Я не нашёл сообщений по: {report_query}\n\n"
                "Проверь, как точно называется чат или username."
            )
            return

        await message.answer(f"🤖 Делаю отчёт по {report_query}...")

        try:
            ai_text = analyze_messages_with_groq(messages)

            await message.answer(
                f"📊 Отчёт по {report_query}\n\n"
                f"{ai_text}"
            )
        except Exception as e:
            print("REPORT BY CHAT ERROR:", e)
            await message.answer(
                "Не смог сделать AI-отчёт. Возможно, лимит Groq. Попробуй чуть позже."
            )

        return

    if (
        "помощь" in lower_text
        or "help" in lower_text
        or "что ты умеешь" in lower_text
        or "команды" in lower_text
    ):
        await message.answer(
            "🤖 Команды:\n\n"
            "📊 Отчёт\n"
            "📢 Отчёт в канал\n"
            "📱 Подключить Telegram\n"
            "📋 Список аккаунтов\n"
            "🗑 Удалить сессию по номеру\n"
            "🔎 Анализ чата\n"
            "❓ Помощь"
        )
        return

    memory_command = detect_command_by_memory(text, user_id)
    intent = memory_command["intent"]
    score = memory_command.get("score", 0)

    print("MEMORY INTENT:", intent, "SCORE:", score)

    if score >= 0.55 and score < 0.72:
        last_unknown_text[user_id] = text

    if intent == "unknown":
        try:
            ai_answer = chat_with_groq(text)
            await message.answer(ai_answer)
        except Exception as e:
            print("❌ CHAT GROQ ERROR:", e)
            await message.answer(
                "Я тебя понял, но Groq сейчас не ответил. Попробуй ещё раз через пару секунд."
            )
        return

    if user_id in last_unknown_text:
        auto_learn_from_previous(last_unknown_text[user_id], text)
        del last_unknown_text[user_id]
        await message.answer("✅ Запомнил исправление.")

    if intent == "send_report_now":
        await message.answer(build_report_text(with_ai=True))
        return

    if intent == "send_report_to_channel":
        if not REPORT_CHAT_ID:
            await message.answer("❌ REPORT_CHAT_ID не указан в .env")
            return

        await message.answer("📊 Собираю новый отчёт...")

        try:
            reports = build_reports_by_accounts(detailed=False)

            if not reports:
                await bot.send_message(REPORT_CHAT_ID, "За этот период новых диалогов нет.")
                return

            for report in reports:
                await bot.send_message(
                    REPORT_CHAT_ID,
                    report["text"],
                    reply_markup=report_keyboard(report["session_name"])
                )

            await message.answer("✅ Новый отчёт отправил в канал")

        except Exception as e:
            print("DIALOG CHANNEL REPORT ERROR:", e)
            await message.answer(f"❌ Ошибка отчёта в канал: {e}")

        return

    if intent == "show_leads":
        await message.answer("🔥 Лидов подключим следующим шагом.")
        return

    if intent == "schedule_report":
        return

    if intent == "analyze_chat_now":
        await message.answer("🔎 Анализ конкретного чата подключим следующим шагом.")
        return

    await message.answer("Команду понял, но действие пока не подключено.")


@dp.message(Command("addchannel"))
async def add_channel_command(message: types.Message):
    await message.answer(
        "📢 Перешли мне любое сообщение из нужного канала или группы.\n\n"
        "Я запомню его ID и буду слать туда отчёты."
    )


@dp.message(lambda m: m.forward_from_chat is not None)
async def forwarded_channel_message(message: types.Message):
    chat = message.forward_from_chat
    channel_id = str(chat.id)
    channel_title = chat.title or channel_id
    owner_user_id = str(message.from_user.id)

    try:
        await asyncio.to_thread(
            save_bot_channels,
            owner_user_id,
            [{
                "channel_id": channel_id,
                "channel_title": channel_title,
            }]
        )
        await message.answer(
            f"✅ Канал сохранён: {channel_title}\n"
            f"ID: {channel_id}\n\n"
            "Теперь при подключении TG ты сможешь выбрать его из списка."
        )
    except Exception as e:
        await message.answer(f"❌ Не смог сохранить канал: {e}")


@dp.callback_query(lambda c: c.data.startswith("timer_pick_channel:"))
async def timer_channel_picked(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    channel_id = parts[1]
    channel_title = parts[2] if len(parts) > 2 else channel_id

    await state.update_data(channel_id=channel_id, channel_title=channel_title)
    await state.set_state(TimerSetup.waiting_day_time)

    await callback.answer()
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=f"✅ Канал: {channel_title}\n\n🌙 В какое время отправлять дневной отчёт?\n\nФормат: 21:00 или 21"
    )


@dp.message(StateFilter(TimerSetup.waiting_day_time))
async def timer_day_time(message: types.Message, state: FSMContext):
    parsed = parse_time(message.text or "")
    if not parsed:
        await message.answer("❌ Не понял время. Напиши в формате 21:00 или просто 21")
        return

    h, m = parsed
    await state.update_data(day_hour=h, day_minute=m)
    await state.set_state(TimerSetup.waiting_night_time)
    await message.answer(
        f"✅ Дневной отчёт: {h:02d}:{m:02d}\n\n🌅 В какое время отправлять ночной отчёт?\n\nФормат: 9:00 или 9"
    )


@dp.message(StateFilter(TimerSetup.waiting_night_time))
async def timer_night_time(message: types.Message, state: FSMContext):
    parsed = parse_time(message.text or "")
    if not parsed:
        await message.answer("❌ Не понял время. Напиши в формате 9:00 или просто 9")
        return

    h, m = parsed
    await state.update_data(night_hour=h, night_minute=m)
    await state.set_state(TimerSetup.confirming)

    data = await state.get_data()
    dh, dm = data["day_hour"], data["day_minute"]
    nh, nm = h, m
    title = data["channel_title"]

    await message.answer(
        f"📋 Проверь настройки:\n\n"
        f"📢 Канал: {title}\n"
        f"🌙 Дневной отчёт: {dh:02d}:{dm:02d}\n"
        f"🌅 Ночной отчёт: {nh:02d}:{nm:02d}\n\n"
        f"Сохранить?",
        reply_markup=build_confirm_keyboard()
    )


@dp.callback_query(lambda c: c.data.startswith("timer_confirm:"))
async def timer_confirm(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split(":")[1]

    if answer == "no":
        await state.clear()
        await callback.answer()
        await bot.send_message(chat_id=callback.from_user.id, text="❌ Отменено. Таймер не изменён.")
        return

    data = await state.get_data()
    ok = await asyncio.to_thread(
        set_timer_settings,
        channel_id=data["channel_id"],
        channel_title=data["channel_title"],
        day_hour=data["day_hour"],
        day_minute=data["day_minute"],
        night_hour=data["night_hour"],
        night_minute=data["night_minute"],
    )

    await state.clear()
    await callback.answer()

    if ok:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=(
                f"✅ Расписание сохранено!\n\n"
                f"📢 {data['channel_title']}\n"
                f"🌙 Дневной: {data['day_hour']:02d}:{data['day_minute']:02d}\n"
                f"🌅 Ночной: {data['night_hour']:02d}:{data['night_minute']:02d}"
            )
        )
    else:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="❌ Ошибка сохранения. Попробуй ещё раз."
        )

@dp.message(lambda m: m.chat.type in ("group", "supergroup"))
async def group_message_handler(message: types.Message):
    text = (message.text or "").strip().lower()

    if text != "claw":
        return

    try:
        channel_id = str(message.chat.id)
        channel_title = message.chat.title or channel_id

        await asyncio.to_thread(
            save_bot_channels,
            "default",
            [{
                "channel_id": channel_id,
                "channel_title": channel_title,
            }]
        )

        await bot.send_message(
            channel_id,
            f"✅ Группа «{channel_title}» подключена.\nТеперь можно привязывать Telegram-аккаунты."
        )

        print(f"✅ Группа сохранена: {channel_title} ({channel_id})")
    except Exception as e:
        print("❌ group_message_handler SAVE ERROR:", e)

@dp.my_chat_member()
async def bot_removed_handler(update: types.ChatMemberUpdated):
    new_status = update.new_chat_member.status

    if new_status in ("left", "kicked", "restricted"):
        channel_id = str(update.chat.id)
        channel_title = update.chat.title or channel_id

        try:
            await asyncio.to_thread(remove_bot_channel, channel_id)
            print(f"🗑 Бот удалён из: {channel_title} ({channel_id}), канал убран из списка")
        except Exception as e:
            print(f"❌ remove_bot_channel ERROR: {e}")

@dp.message(lambda m: m.chat.type == "private" and (m.text or "").lower().strip() in (
    "загрузи историю", "загрузить историю", "прочитай чаты", "читай историю"
))
async def load_history_command(message: types.Message):
    from zoneinfo import ZoneInfo
    from datetime import timezone as dt_timezone
    from telethon.sessions import StringSession
    from telethon import TelegramClient
    from telethon.tl.types import User as TelethonUser

    from supabase_db import supabase
    from datetime import datetime, timedelta
    from telegram_connect import API_ID, API_HASH
    KYIV_TZ = ZoneInfo("Europe/Kyiv")

    await message.answer("⏳ Загружаю историю за текущую смену по всем аккаунтам...")

    try:
        accounts_result = await asyncio.to_thread(
            lambda: supabase.table("telegram_accounts").select("*").eq("active", True).execute()
        )
        accounts = accounts_result.data or []

        if not accounts:
            await message.answer("❌ Нет подключённых аккаунтов.")
            return

        total_new = 0
        total_skipped = 0

        # Определяем начало текущей смены
        now = datetime.now(KYIV_TZ)
        day_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        night_start = now.replace(hour=21, minute=0, second=0, microsecond=0)

        if day_start <= now < night_start:
            shift_start = day_start
        elif now >= night_start:
            shift_start = night_start
        else:
            from datetime import timedelta
            shift_start = night_start - timedelta(days=1)

        shift_start_utc = shift_start.astimezone(dt_timezone.utc)

        for account in accounts:
            session_string = account.get("session_string")
            username = account.get("username") or account.get("phone") or account.get("session_name")

            try:
                await message.answer(f"🔄 Читаю чаты: @{username}...")

                client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
                await client.connect()

                if not await client.is_user_authorized():
                    await message.answer(f"⚠️ Аккаунт @{username} не авторизован, пропускаю.")
                    await client.disconnect()
                    continue

                # Загружаем уже существующие сообщения из Supabase для этого аккаунта
                # чтобы избежать дублей — индексируем по (dialog_id, message_date)
                existing_result = await asyncio.to_thread(
                    lambda: supabase.table("telegram_messages")
                    .select("dialog_id, message_date")
                    .eq("account_session_name", account.get("session_name"))
                    .gte("message_date", shift_start_utc.isoformat())
                    .execute()
                )

                existing_keys = set()
                for row in (existing_result.data or []):
                    d_id = str(row.get("dialog_id") or "")
                    m_date = str(row.get("message_date") or "")
                    if d_id and m_date:
                        existing_keys.add((d_id, m_date[:19]))  # до секунд

                new_count = 0
                skip_count = 0

                async for dialog in client.iter_dialogs(limit=150):
                    try:
                        entity = dialog.entity
                        if not isinstance(entity, TelethonUser):
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

                        async for msg in client.iter_messages(entity, limit=100):
                            if not msg.date:
                                continue

                            msg_date_kyiv = msg.date.astimezone(KYIV_TZ)

                            if msg_date_kyiv < shift_start:
                                break

                            if not msg.raw_text:
                                continue

                            # Проверяем дубль: (dialog_id, дата до секунд)
                            msg_date_iso = msg.date.astimezone(dt_timezone.utc).isoformat()
                            dedup_key = (str(dialog_id), msg_date_iso[:19])

                            if dedup_key in existing_keys:
                                skip_count += 1
                                continue

                            direction = "outgoing" if msg.out else "incoming"

                            await asyncio.to_thread(
                                lambda: supabase.table("telegram_messages").insert({
                                    "account_session_name": account.get("session_name"),
                                    "account_username": account.get("username") or account.get("phone"),
                                    "dialog_id": str(dialog_id),
                                    "dialog_username": dialog_username,
                                    "dialog_name": dialog_name,
                                    "direction": direction,
                                    "text": msg.raw_text,
                                    "message_date": msg.date.astimezone(dt_timezone.utc).isoformat(),
                                }).execute()
                            )

                            existing_keys.add(dedup_key)
                            new_count += 1

                    except Exception as e:
                        print(f"❌ Ошибка диалога [{username}]: {e}", flush=True)

                await client.disconnect()

                total_new += new_count
                total_skipped += skip_count

                await message.answer(
                    f"✅ @{username}: новых сообщений — {new_count}, "
                    f"уже было — {skip_count}"
                )

            except Exception as e:
                print(f"❌ LOAD HISTORY ACCOUNT ERROR [{username}]: {e}", flush=True)
                await message.answer(f"❌ Ошибка аккаунта @{username}: {e}")

        await message.answer(
            f"✅ Загрузка завершена.\n\n"
            f"📥 Новых сообщений записано: {total_new}\n"
            f"⏭ Уже было в базе: {total_skipped}\n\n"
            f"Теперь можешь запросить отчёт — все лиды учтены."
        )

    except Exception as e:
        print("❌ LOAD HISTORY ERROR:", e)
        await message.answer(f"❌ Ошибка загрузки истории: {e}")

@dp.channel_post()
async def channel_post_handler(message: types.Message):
    print("CHANNEL ID:", message.chat.id)
    print("CHANNEL TITLE:", message.chat.title)

    text = (message.text or "").strip().lower()

    if text != "claw":
        return

    try:
        channel_id = str(message.chat.id)
        channel_title = message.chat.title or channel_id
        owner_user_id = "default"

        await asyncio.to_thread(
            save_bot_channels,
            owner_user_id,
            [{
                "channel_id": channel_id,
                "channel_title": channel_title,
            }]
        )

        await bot.send_message(
            channel_id,
            f"✅ Канал «{channel_title}» подключён.\nТеперь можно привязывать Telegram-аккаунты."
        )

        print(f"✅ Канал сохранён: {channel_title} ({channel_id})")
    except Exception as e:
        print("❌ channel_post_handler SAVE ERROR:", e)

def build_operator_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Заход", callback_data="op_stat:zahod"),
                InlineKeyboardButton(text="📋 Бронь", callback_data="op_stat:bron"),
                InlineKeyboardButton(text="🔄 Разворот", callback_data="op_stat:razvorot"),
            ],
            [
                InlineKeyboardButton(text="✅ Готово", callback_data="op_stat:done"),
            ]
        ]
    )


@dp.callback_query(lambda c: c.data.startswith("op_stat:"))
async def op_stat_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split(":")[1]

    if action == "done":
        state = operator_poll_state.get(user_id)
        if not state:
            await callback.answer("Нет активного опроса", show_alert=True)
            return

        # Сохраняем в Supabase
        from supabase_db import supabase
        slot = state.get("slot", "unknown")
        pc_name = state.get("pc_name", "-")
        try:
            await asyncio.to_thread(
                lambda: supabase.table("operator_stats").upsert({
                    "telegram_id": user_id,
                    "pc_name": pc_name,
                    "shift_slot": slot,
                    "zahody": state.get("zahody", 0),
                    "broni": state.get("broni", 0),
                    "razvoroty": state.get("razvoroty", 0),
                }, on_conflict="shift_slot").execute()
            )
        except Exception as e:
            print("❌ op_stat save ERROR:", e)

        del operator_poll_state[user_id]
        await callback.message.edit_text(
            f"✅ Записано:\n"
            f"📥 Заходы: {state.get('zahody', 0)}\n"
            f"📋 Брони: {state.get('broni', 0)}\n"
            f"🔄 Развороты: {state.get('razvoroty', 0)}"
        )
        await callback.answer()
        return

    # Спрашиваем количество
    labels = {"zahod": "заходов", "bron": "броней", "razvorot": "разворотов"}
    label = labels.get(action, action)

    if user_id not in operator_poll_state:
        operator_poll_state[user_id] = {"zahody": 0, "broni": 0, "razvoroty": 0}

    operator_poll_state[user_id]["waiting_for"] = action
    await callback.answer()
    await callback.message.answer(f"Сколько {label}? Введи цифру:")


@dp.message(lambda m: m.chat.type == "private" and m.from_user.id in operator_poll_state and operator_poll_state[m.from_user.id].get("waiting_for"))
async def op_stat_count_input(message: types.Message):
    user_id = message.from_user.id
    state = operator_poll_state[user_id]
    action = state.get("waiting_for")

    try:
        count = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число, например: 3")
        return

    key_map = {"zahod": "zahody", "bron": "broni", "razvorot": "razvoroty"}
    label_map = {"zahod": "заходов", "bron": "броней", "razvorot": "разворотов"}

    key = key_map.get(action)
    if key:
        state[key] = count

    state["waiting_for"] = None

    await message.answer(
        f"✅ {label_map.get(action, action)}: {count}\n\n"
        f"📥 Заходы: {state.get('zahody', 0)}\n"
        f"📋 Брони: {state.get('broni', 0)}\n"
        f"🔄 Развороты: {state.get('razvoroty', 0)}\n\n"
        "Нажми ещё кнопку или ✅ Готово",
        reply_markup=build_operator_keyboard()
    )


@dp.message(lambda m: m.chat.type == "private" and (m.text or "").lower().startswith("добавить оператора"))
async def add_operator_handler(message: types.Message):
    from supabase_db import supabase
    text = message.text.strip()

    username_match = re.search(r"@(\w+)", text)
    pc_match = re.search(r"ПК[:\s]+(\S+)", text, re.IGNORECASE)
    id_match = re.search(r"\bid(\s*)[:\s]+(\d+)", text, re.IGNORECASE)

    if not username_match and not id_match:
        await message.answer(
            "❌ Укажи username или ID. Примеры:\n"
            "добавить оператора @username ПК:D3\n"
            "добавить оператора ID:123456789 ПК:D3"
        )
        return

    pc_name = pc_match.group(1) if pc_match else "-"

    if id_match:
        telegram_id = int(id_match.group(2))
        username = username_match.group(1) if username_match else str(telegram_id)
    else:
        username = username_match.group(1)
        try:
            chat = await bot.get_chat(f"@{username}")
            telegram_id = chat.id
        except Exception:
            await message.answer(
                f"❌ Не могу найти @{username} автоматически.\n\n"
                f"Попробуй добавить по ID:\n"
                f"добавить оператора @{username} ID:123456789 ПК:{pc_name}\n\n"
                f"ID можно узнать через @userinfobot"
            )
            return

    try:
        await asyncio.to_thread(
            lambda: supabase.table("operators").upsert({
                "telegram_id": telegram_id,
                "username": username,
                "pc_name": pc_name,
                "active": True,
            }, on_conflict="telegram_id").execute()
        )
        await message.answer(f"✅ Оператор @{username} (ПК: {pc_name}, ID: {telegram_id}) добавлен!")
    except Exception as e:
        await message.answer(f"❌ Ошибка сохранения: {e}")


@dp.message(lambda m: m.chat.type == "private" and (m.text or "").lower().strip() in ("список операторов", "операторы"))
async def list_operators_handler(message: types.Message):
    from supabase_db import supabase
    result = await asyncio.to_thread(
        lambda: supabase.table("operators").select("*").eq("active", True).execute()
    )
    ops = result.data or []
    if not ops:
        await message.answer("Операторов нет. Добавь: добавить оператора @username ПК:D3")
        return

    lines = ["👥 Операторы:"]
    for op in ops:
        lines.append(f"• @{op.get('username')} — ПК: {op.get('pc_name')} (ID: {op.get('telegram_id')})")
    await message.answer("\n".join(lines))

@dp.message(lambda m: m.chat.type == "private" and (m.text or "").lower().startswith("удалить оператора"))
async def remove_operator_handler(message: types.Message):
    from supabase_db import supabase
    text = message.text.strip()

    username_match = re.search(r"@(\w+)", text)
    if not username_match:
        await message.answer("❌ Укажи username. Пример:\nудалить оператора @username")
        return

    username = username_match.group(1)

    try:
        await asyncio.to_thread(
            lambda: supabase.table("operators").update({"active": False}).eq("username", username).execute()
        )
        await message.answer(f"✅ Оператор @{username} удалён.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

async def main():
    print("BOT STARTED")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
