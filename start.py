import os
import asyncio
from aiohttp import web

from bot import main as bot_main
from multworker import main as multworker_main
from report_scheduler import main as report_scheduler_main


async def health(request):
    return web.Response(text="CLAW-AI is running")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)

    port = int(os.environ.get("PORT", 10000))

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"✅ WEB SERVER STARTED ON PORT {port}", flush=True)


async def safe_run(name, func):
    while True:
        try:
            print(f"🚀 STARTING {name}", flush=True)
            await func()
        except Exception as e:
            print(f"❌ {name} CRASHED: {e}", flush=True)
            print(f"🔁 RESTARTING {name} IN 10 SEC", flush=True)
            await asyncio.sleep(10)


async def main():
    print("🔥 START.PY LAUNCHED", flush=True)

    await start_web_server()

    asyncio.create_task(safe_run("BOT", bot_main))
    asyncio.create_task(safe_run("MULTWORKER", multworker_main))
    asyncio.create_task(safe_run("REPORT_SCHEDULER", report_scheduler_main))

    print("✅ ALL TASKS CREATED", flush=True)

    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
