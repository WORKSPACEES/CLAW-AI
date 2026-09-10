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
            model="openai/gpt-oss-20b",
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
            max_tokens=1500,
            reasoning_effort="low",
            reasoning_format="hidden",
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
            max_tokens=1000,
            reasoning_effort="low",
            reasoning_format="hidden",
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
            max_tokens=1200,
            reasoning_effort="low",
            reasoning_format="hidden",
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
            role = "Клиент"
        elif direction == "outgoing":
            role = "Менеджер"
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
Ты анализируешь Telegram-диалог между клиентом и менеджером эскорт-услуг.

РОЛИ:
- "Клиент:" — входящие сообщения (incoming)
- "Менеджер:" — исходящие сообщения (outgoing)
Не путай роли.

КРИТЕРИИ ОЦЕНКИ ЛИДА:

🔴 ПЛОХОЙ ЛИД (вероятность захода 0-10%):
- Сразу просит фото без интереса к условиям
- Говорит "подумаю", "не подходит", "дорого"
- Грубит менеджеру
- Прощается и уходит
- Не отвечает после первого сообщения

🟡 СРЕДНИЙ ЛИД (вероятность 20-50%):
- Договорились на завтра или на "потом"
- Сказал "освобожусь — напишу"
- Сказал "напишу позже"
- Интерес есть, но конкретики нет
- Спрашивал фото и условия, но не договорились о времени

🟢 ХОРОШИЙ ЛИД (вероятность 60-80%):
- Спросил о встрече, сумме, что входит
- Посмотрел фото и заинтересовался
- Обсудили сумму — клиент сказал что подходит
- Договорились на конкретное время СЕГОДНЯ
- Обсудили такси

🔥 ЗАХОД (вероятность 80-100%):
- Клиент вызвал такси и скинул ссылку → 80%
- Такси приехало, клиент написал что идёт к такси / забирает такси / менеджер написал что приехал → и клиент не отказался в течение 30 минут → 100%

ФОРМАТ ОТВЕТА (строго JSON, без markdown):
{{
    "детали": "Подробно что происходило в диалоге: о чём говорили, что клиент спрашивал, что менеджер ответил, договорились ли о времени/сумме/такси",
    "анализ": "Оценка ситуации: насколько клиент горячий, что мешает заходу или что помогает, поведение клиента",
    "вероятность": "XX% — одна цифра с кратким обоснованием"
}}

ВАЖНО:
- Не придумывай факты которых нет в переписке
- Пиши конкретно что было в чате
- Вероятность ставь строго по критериям выше
- Если такси упомянуто — это важный сигнал, обязательно укажи
- Если клиент не ответил после договорённости — укажи это

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
                        "Анализируешь Telegram-переписки менеджеров с клиентами. "
                        "Оцениваешь вероятность реального захода клиента по чётким критериям. "
                        "Отвечай строго валидным JSON без markdown и пояснений."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.2,
            max_tokens=1200,
            reasoning_effort="low",
            reasoning_format="hidden",
        )

        raw = response.choices[0].message.content.strip()
        parsed = safe_json_loads(raw)

        if parsed:
            return {
                "diagnosis": parsed.get("вероятность", "Неизвестно"),
                "detail": parsed.get("детали", "Нет деталей"),
                "result": parsed.get("анализ", "Неизвестно"),
                "manager_action": "",
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
