# -*- coding: utf-8 -*-
"""
Бот для Render/Replit: HTTP-сервер в фоновом потоке (Render проверяет PORT),
бот в главном потоке (python-telegram-bot v21+ требует главный поток).
Запуск: python bot_replit.py
"""
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
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


if __name__ == "__main__":
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        print("TELEGRAM_BOT_TOKEN not set", flush=True)
        sys.exit(1)

    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()

    from bot import main
    main()
