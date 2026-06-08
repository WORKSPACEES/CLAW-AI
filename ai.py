import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY)


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

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": "Ты аналитик Telegram-переписок. Отвечай кратко и по делу на русском."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )

    return response.choices[0].message.content

import json


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
    )

    raw = response.choices[0].message.content.strip()

    try:
        return json.loads(raw)
    except Exception:
        return {
            "lead": False,
            "topic": "parse_error",
            "priority": "low",
            "needs_manual_reply": False,
            "raw": raw,
        }

def chat_with_groq(text):
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