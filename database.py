import sqlite3

conn = sqlite3.connect("analyzer.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER,
    chat_id INTEGER,
    chat_name TEXT,
    sender_name TEXT,
    text TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()


def save_message(
    telegram_id,
    chat_id,
    chat_name,
    sender_name,
    text
):
    cursor.execute("""
    INSERT INTO messages (
        telegram_id,
        chat_id,
        chat_name,
        sender_name,
        text
    )
    VALUES (?, ?, ?, ?, ?)
    """, (
        telegram_id,
        chat_id,
        chat_name,
        sender_name,
        text
    ))

def get_today_stats():
    cursor.execute("""
    SELECT 
        COUNT(*) as total_messages,
        COUNT(DISTINCT telegram_id) as unique_people
    FROM messages
    WHERE date(created_at) = date('now')
    """)

    row = cursor.fetchone()

    cursor.execute("""
    SELECT text
    FROM messages
    WHERE date(created_at) = date('now')
    """)

    texts = [r[0] for r in cursor.fetchall() if r[0]]

    triggers = [
        "работа",
        "дубай",
        "dubai",
        "кастинг",
        "жилье",
        "жильё",
        "перелет",
        "перелёт",
        "модель",
        "заработок",
        "цена",
        "условия",
    ]

    trigger_counts = {}

    for text in texts:
        lower = text.lower()
        for word in triggers:
            if word in lower:
                trigger_counts[word] = trigger_counts.get(word, 0) + 1

    return {
        "total_messages": row[0] or 0,
        "unique_people": row[1] or 0,
        "trigger_counts": trigger_counts,
    }

def get_today_messages(limit=50):
    cursor.execute("""
    SELECT sender_name, chat_name, text
    FROM messages
    WHERE date(created_at) = date('now')
    ORDER BY created_at DESC
    LIMIT ?
    """, (limit,))

    rows = cursor.fetchall()

    return [
        f"{sender_name} / {chat_name}: {text}"
        for sender_name, chat_name, text in rows
        if text
    ]

def get_messages_by_chat_query(query, limit=80):
    search = f"%{query.lower()}%"

    cursor.execute("""
    SELECT sender_name, chat_name, text, created_at
    FROM messages
    WHERE lower(chat_name) LIKE ?
    ORDER BY created_at DESC
    LIMIT ?
    """, (search, limit))

    rows = cursor.fetchall()

    return [
        f"{created_at} | {sender_name} / {chat_name}: {text}"
        for sender_name, chat_name, text, created_at in rows
        if text
    ]

    conn.commit()
