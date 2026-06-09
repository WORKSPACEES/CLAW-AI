import os
import json
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)


def safe_json_loads(raw):
    try:
        return json.loads(raw)
    except Exception:
        pass

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1

        if start != -1 and end != 0:
            return json.loads(raw[start:end])
    except Exception:
        pass

    return None


def analyze_messages_with_groq(messages):
    if not messages:
        return "Пока нет сообщений для AI-анализа."

    text_block = "\n".join(messages[-50:])

    prompt = f"""
Проанализируй переписки Telegram.

Найди:
1. Горячих лидов
2. Кто спрашивает про работу
3. Кто спрашивает про Дубай
4. Кто спрашивает цену/условия
5. Какие сообщения требуют ручного ответа
6. Краткий вывод для отчёта

Сообщения:
{text_block}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": "Ты аналитик Telegram-переписок. Отвечай кратко и по делу на русском.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.3,
            max_tokens=700,
        )

        return response.choices[0].message.content

    except Exception as e:
        print("❌ GROQ ANALYZE MESSAGES ERROR:", e)
        return "AI-анализ временно недоступен. Проверь лимит Groq или ключ API."


def analyze_single_message_for_lead(text, chat_name="", sender_name=""):
    if not text:
        return {
            "lead": False,
            "topic": "empty",
            "priority": "low",
            "needs_manual_reply": False,
        }

    prompt = f"""
Проанализируй Telegram-сообщение как лид.

Верни только JSON без markdown.

Поля:
lead: true/false
topic: краткая тема
priority: low/medium/high
needs_manual_reply: true/false

Контекст:
Чат: {chat_name}
Отправитель: {sender_name}
Сообщение: {text}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": "Ты аналитик лидов. Отвечай строго валидным JSON без пояснений.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
            max_tokens=400,
        )

        raw = response.choices[0].message.content.strip()
        parsed = safe_json_loads(raw)

        if parsed:
            return {
                "lead": bool(parsed.get("lead", False)),
                "topic": parsed.get("topic", "unknown"),
                "priority": parsed.get("priority", "low"),
                "needs_manual_reply": bool(parsed.get("needs_manual_reply", False)),
            }

        return {
            "lead": False,
            "topic": "parse_error",
            "priority": "low",
            "needs_manual_reply": False,
            "raw": raw,
        }

    except Exception as e:
        print("❌ GROQ SINGLE MESSAGE ERROR:", e)

        return {
            "lead": False,
            "topic": "groq_error",
            "priority": "low",
            "needs_manual_reply": False,
        }


def chat_with_groq(text):
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты Telegram AI-ассистент проекта ANALIZATOR. "
                        "Общайся живо, по-русски, кратко и понятно. "
                        "Если пользователь просто здоровается — поздоровайся. "
                        "Если спрашивает обычный вопрос — ответь. "
                        "Если просит отчёты, аналитику, лидов или расписание — скажи, что могу выполнить это через команды системы."
                    ),
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
            temperature=0.4,
            max_tokens=500,
        )

        return response.choices[0].message.content

    except Exception as e:
        print("❌ GROQ CHAT ERROR:", e)
        return "Groq сейчас не ответил. Попробуй ещё раз через пару секунд."


def analyze_dialog_with_groq(dialog_messages):
    if not dialog_messages:
        return {
            "diagnosis": "Нет данных",
            "detail": "Сообщений нет.",
            "result": "Неизвестно",
            "manager_action": "Проверить вручную.",
        }

    dialog_lines = []

    for msg in dialog_messages[-50:]:
        text = msg.get("text") or ""

        if not text:
            continue

        direction = msg.get("direction")

        if direction == "incoming":
            role = "Менеджер"
        elif direction == "outgoing":
            role = "Клиент"
        else:
            role = "Неизвестно"

        dialog_lines.append(f"{role}: {text}")

    dialog_text = "\n".join(dialog_lines)

    if not dialog_text.strip():
        return {
            "diagnosis": "Нет текста",
            "detail": "В диалоге нет текстовых сообщений.",
            "result": "Неизвестно",
            "manager_action": "Проверить вручную.",
        }

    prompt = f"""
Ты анализируешь один Telegram-диалог между клиентом и менеджером.

ВАЖНО ПО РОЛЯМ:
- Клиент = OUTGOING сообщения.
- Менеджер = INCOMING сообщения.
- Не путай роли местами.
- Если строка начинается с "Клиент:" — это клиент.
- Если строка начинается с "Менеджер:" — это менеджер.

Задача:
- Не считай просто количество сообщений.
- Пойми смысл переписки.
- Определи, есть ли интерес, сомнение, отказ, ожидание ответа или хороший лид.
- Если последнее сообщение клиента осталось без ответа — обязательно укажи это.
- Если клиент подтвердил встречу, время, место или согласие — укажи это.
- Если менеджер уже ответил и диалог выглядит нормально — напиши, что ситуация в работе.
- Пиши кратко, но конкретно.
- Не придумывай факты, которых нет в переписке.

Верни строго JSON без markdown и без пояснений.

Формат:
{{
    "diagnosis": "короткий диагноз ситуации",
    "detail": "конкретно что произошло в диалоге",
    "result": "итог диалога",
    "manager_action": "что нужно сделать менеджеру"
}}

Примеры:
{{
    "diagnosis": "Клиент интересуется ценой",
    "detail": "Клиент спросил стоимость и условия. Менеджер ответил, но клиент ещё не подтвердил решение.",
    "result": "Есть интерес, но сделка не закрыта",
    "manager_action": "Написать follow-up и предложить конкретное действие."
}}

{{
    "diagnosis": "Клиент ждёт ответ",
    "detail": "Последнее сообщение написал клиент, менеджер после этого не ответил.",
    "result": "Нужен ответ",
    "manager_action": "Ответить клиенту как можно быстрее."
}}

{{
    "diagnosis": "Клиент подтвердил встречу",
    "detail": "Клиент согласился на встречу или подтвердил детали. Менеджер дал ответ по ситуации.",
    "result": "Хороший лид",
    "manager_action": "Контролировать встречу и не потерять клиента."
}}

Переписка:
{dialog_text}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты опытный руководитель отдела продаж. "
                        "Ты анализируешь Telegram-переписки для отчёта. "
                        "Строго соблюдай роли: Клиент — это OUTGOING, Менеджер — это INCOMING. "
                        "Отвечай только валидным JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
            max_tokens=700,
        )

        raw = response.choices[0].message.content.strip()
        parsed = safe_json_loads(raw)

        if parsed:
            return {
                "diagnosis": parsed.get("diagnosis", "Неизвестно"),
                "detail": parsed.get("detail", "Нет деталей"),
                "result": parsed.get("result", "Неизвестно"),
                "manager_action": parsed.get("manager_action", "Проверить вручную"),
            }

        return {
            "diagnosis": "Ошибка анализа",
            "detail": raw[:500],
            "result": "Не удалось определить",
            "manager_action": "Проверить вручную",
        }

    except Exception as e:
        print("❌ GROQ DIALOG ANALYSIS ERROR:", e)

        return {
            "diagnosis": "AI-анализ недоступен",
            "detail": "Groq не смог обработать диалог.",
            "result": "Неизвестно",
            "manager_action": "Проверить вручную",
        }
