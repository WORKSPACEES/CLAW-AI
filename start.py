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

    print(f"✅ Web server started on port {port}")


async def safe_run(name, coro):
    while True:
        try:
            print(f"🚀 Starting {name}")
            await coro()
        except Exception as e:
            print(f"❌ {name} crashed:", e)
            print(f"🔁 Restarting {name} in 10 seconds...")
            await asyncio.sleep(10)


async def main():
    await start_web_server()

    await asyncio.gather(
        safe_run("bot.py", bot_main),
        safe_run("multworker.py", multworker_main),
        safe_run("report_scheduler.py", report_scheduler_main),
    )


if __name__ == "__main__":
    asyncio.run(main())