# -*- coding: utf-8 -*-
"""
Точка входа бота: HTTP-сервер в фоне (PORT), бот в главном потоке.
По умолчанию запускается бот v2 (без LLM, только Whisper для голоса).
Чтобы вернуться к боту v1 (с LLM): задай BOT_VERSION=1 или BOT_VERSION=v1.
"""
import os
import sys
import time
import asyncio
import threading
import logging
import importlib
from http.server import HTTPServer, BaseHTTPRequestHandler

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "8080"))
# BOT_VERSION=1 или v1 — запуск старого бота (bot.py с LLM). Иначе — bot_v2.
_bot_ver = os.environ.get("BOT_VERSION", "").strip().lower()
USE_V1 = _bot_ver in ("1", "v1")

logger = logging.getLogger("bot_replit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

bot_healthy = True


class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        status = "OK" if bot_healthy else "RESTARTING"
        self.wfile.write(status.encode())

    def log_message(self, format, *args):
        pass


def run_http():
    server = HTTPServer(("0.0.0.0", PORT), PingHandler)
    server.serve_forever()


def run_bot():
    global bot_healthy
    max_retries = 10
    base_delay = 15

    _stamp = ""
    try:
        _p = os.path.join(os.path.dirname(__file__) or ".", "deploy_stamp.txt")
        if os.path.exists(_p):
            with open(_p, encoding="utf-8") as _f:
                _stamp = _f.read().strip()
    except Exception:
        pass
    version = "v1" if USE_V1 else "v2"
    logger.info("DEPLOY_STAMP: %s (bot %s)", _stamp or "none", version)

    for attempt in range(1, max_retries + 1):
        try:
            bot_healthy = True
            logger.info("Запуск бота %s (попытка %d/%d)", version, attempt, max_retries)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            if USE_V1:
                import bot as bot_module
            else:
                import bot_v2 as bot_module  # по умолчанию v2
            importlib.reload(bot_module)
            bot_module.main()

        except SystemExit:
            logger.info("Бот завершился (SystemExit)")
            break
        except Exception as e:
            bot_healthy = False
            logger.error("Бот упал: %s", e)
        finally:
            try:
                loop = asyncio.get_event_loop()
                if not loop.is_closed():
                    loop.close()
            except Exception:
                pass

        if attempt < max_retries:
            wait = base_delay * attempt
            logger.info("Перезапуск через %dс...", wait)
            time.sleep(wait)
        else:
            logger.critical("Бот не смог запуститься после %d попыток", max_retries)
            sys.exit(1)


if __name__ == "__main__":
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        print("TELEGRAM_BOT_TOKEN not set", flush=True)
        sys.exit(1)

    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()

    run_bot()
