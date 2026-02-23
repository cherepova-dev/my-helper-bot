# -*- coding: utf-8 -*-
"""
Бот для Replit: polling + простой HTTP-сервер для keep-alive (UptimeRobot).
Запуск на Replit: Run -> python bot_replit.py
"""
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Токен — на Replit задай в Secrets (Tools -> Secrets): TELEGRAM_BOT_TOKEN
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8785603117:AAGWVVEWSVbIc_ZZDhd26OprknT0e6Ldh1Q")

PORT = int(os.environ.get("PORT", "8080"))


class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass


def run_http():
    server = HTTPServer(("0.0.0.0", PORT), PingHandler)
    server.serve_forever()


def run_bot():
    from bot import main
    main()


if __name__ == "__main__":
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        print("Задай TELEGRAM_BOT_TOKEN в Replit Secrets")
        exit(1)
    threading.Thread(target=run_http, daemon=True).start()
    run_bot()
