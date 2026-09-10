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
            model="openai/gpt-oss-20b",
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
            model="openai/gpt-oss-20b",
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
Перед тобой переписка из Telegram. "Клиент:" — это лид, "Менеджер:" — наш сотрудник.
Твоя задача — понять, чем закончился разговор и насколько реально клиент придёт.

КАК ЧИТАТЬ:
Читай сверху вниз, но главное — концовка. Важно не сколько всего обсудили,
а на чём остановились в последнем сообщении и кто остался ходить.
Если одно и то же сообщение повторяется подряд несколько раз — это сбой записи,
считай его за одно и не пиши, что менеджер «многократно писал».

ШКАЛА ВЕРОЯТНОСТИ:
100% — клиент едет: такси приехало, клиент сел или подтвердил, что выходит
80%  — клиент вызвал такси, скинул ссылку на машину
60-75% — договорились на конкретное время сегодня, сумма озвучена и принята
40-55% — сумма и формат обсуждены, клиент согласен, но время не назначено
20-35% — интерес есть, отложил: «завтра», «позже», «напишу как освобожусь»
5-15%  — только спросил цену или фото и пропал, либо разговор оборвался
0%     — отказ, «дорого», «не подходит», грубость, клиент попрощался

КАК ПИСАТЬ:
Пиши как живой руководитель, который пересказывает разговор коллеге.
Короткими фразами, по факту, без канцелярита.
Опиши что было: о чём спросил клиент, что ответил менеджер, до чего дошли.
Называй конкретику из переписки — суммы, время, город, что именно спрашивали.

ЧЕГО НЕ ДЕЛАТЬ:
Не перечисляй, чего в диалоге НЕ было. Фразы вида «нет упоминания такси,
нет времени, нет даты» — запрещены. Описывай только то, что произошло.
Не придумывай фактов, которых нет в переписке.
Не пиши общими словами «клиент проявил интерес» — объясни, в чём именно.

ФОРМАТ — строго JSON, без markdown и без пояснений вокруг:
{{
    "детали": "Что происходило: кто что спросил, что ответили, до чего договорились. 2-4 предложения.",
    "анализ": "Почему именно такая вероятность. Что тянет вниз или вверх. Что делать менеджеру дальше. 1-3 предложения.",
    "вероятность": "XX%"
}}

Переписка:
{dialog_text}
"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты руководитель отдела продаж, разбираешь переписки своих "
                        "менеджеров. Говоришь по делу, без воды и без канцелярита. "
                        "Оцениваешь по концовке разговора, а не по количеству "
                        "проговорённых пунктов. Отвечаешь строго валидным JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.3,
            max_tokens=2000,
            reasoning_effort="medium",
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
