import os
import asyncio
from pathlib import Path
from telethon import TelegramClient

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def load_env_manual(path):
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


async def main():
    if ENV_PATH.exists():
        load_env_manual(ENV_PATH)

    api_id = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")

    client = TelegramClient("dialog_monitor_session", api_id, api_hash)

    await client.start()

    me = await client.get_me()
    print("✅ Новая сессия создана")
    print("Аккаунт:", me.username or me.id)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
