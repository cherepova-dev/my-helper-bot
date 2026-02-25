# -*- coding: utf-8 -*-
"""
Бот для Render/Replit: HTTP-сервер в фоновом потоке (Render проверяет PORT),
бот в главном потоке (python-telegram-bot v21+ требует главный поток).
При падении бот автоматически перезапускается с новым event loop.
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

    for attempt in range(1, max_retries + 1):
        try:
            bot_healthy = True
            logger.info("Запуск бота (попытка %d/%d)", attempt, max_retries)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            import bot as bot_module
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
