import asyncio
import os
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from dotenv import load_dotenv
from aiogram.types import FSInputFile
from telegram_connect import start_login, confirm_code, list_accounts, delete_account_by_phone
from dialog_report import build_report, build_reports_by_accounts
from pathlib import Path
import json
import re

from database import get_today_stats, get_today_messages, get_messages_by_chat_query
from ai import analyze_messages_with_groq, chat_with_groq
from command_memory import detect_command_by_memory, auto_learn_from_previous
from image_ai import build_image_url

load_dotenv()

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
                )
            ]
        ]
    )

@dp.callback_query(lambda c: c.data.startswith("full_report_account:"))
async def full_report_callback(callback: types.CallbackQuery):
    try:
        session_name = callback.data.split(":", 1)[1]

        await callback.answer("📩 Отправляю развёрнутый отчёт в личку")

        reports = build_reports_by_accounts(detailed=True)

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

        await bot.send_message(
            chat_id=callback.from_user.id,
            text=needed_report["text"]
        )

    except Exception as e:
        print("FULL REPORT CALLBACK ERROR:", e)
        await callback.answer("Ошибка развёрнутого отчёта", show_alert=True)

@dp.message()
async def admin_chat(message: types.Message):
    text = message.text or ""

    if text.startswith("/"):
        return

    user_id = message.from_user.id
    lower_text = text.lower()

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
        if not REPORT_CHAT_ID:
            await message.answer("❌ REPORT_CHAT_ID не указан")
            return

        await message.answer("📊 Собираю отчёт и отправляю в канал...")

        try:
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

        except Exception as e:
            print("DIALOG CHANNEL REPORT ERROR:", e)
            await message.answer(f"❌ Ошибка отправки отчёта в канал: {e}")

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

        if state["step"] == "waiting_ad_name":
            state["ad_name"] = text.strip()
            state["step"] = "waiting_pc_name"
            await message.answer("Окей. Какой ПК / оператор?")
            return

        if state["step"] == "waiting_pc_name":
            state["pc_name"] = text.strip()
            state["step"] = "waiting_phone"
            await message.answer("Теперь пришли номер Telegram в формате +380...")
            return

        if state["step"] == "waiting_phone":
            phone = text.strip()
            state["phone"] = phone

            await message.answer("📩 Отправляю код в Telegram...")

            try:
                await start_login(
                    user_id,
                    phone,
                    ad_name=state.get("ad_name"),
                    pc_name=state.get("pc_name"),
                    operator_name=state.get("pc_name"),
                )

                state["step"] = "waiting_code"
                await message.answer("✅ Код отправлен. Теперь пришли код из Telegram.")

            except Exception as e:
                await message.answer(f"❌ Ошибка отправки кода: {e}")

            return

        if state["step"] == "waiting_code":
            code = text.strip().replace(" ", "")
            result = await confirm_code(user_id, code)

            if result["ok"]:
                meta = load_account_meta()

                meta[str(user_id)] = {
                    "ad_name": state.get("ad_name"),
                    "pc_name": state.get("pc_name"),
                    "phone": state.get("phone"),
                }

                save_account_meta(meta)
                del login_state[user_id]

            await message.answer(result["message"])
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
        login_state[user_id] = {
            "step": "waiting_ad_name",
            "ad_name": None,
            "pc_name": None,
            "phone": None,
        }
        await message.answer("Окей. Какая реклама?")
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
        await message.answer("✅ Понял. Расписание отчётов подключим следующим шагом.")
        return

    if intent == "analyze_chat_now":
        await message.answer("🔎 Анализ конкретного чата подключим следующим шагом.")
        return

    await message.answer("Команду понял, но действие пока не подключено.")


@dp.channel_post()
async def channel_post_handler(message: types.Message):
    print("CHANNEL ID:", message.chat.id)
    print("CHANNEL TITLE:", message.chat.title)


async def main():
    print("BOT STARTED")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
