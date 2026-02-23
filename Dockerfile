# Переиспользуемый образ: тот же Dockerfile подойдёт для бота и для будущего сервиса.
FROM python:3.12-slim

WORKDIR /app

# ffmpeg для конвертации голосовых сообщений (ogg → wav)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Зависимости (кэш слоя)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY . .

# На Render: bot_replit.py (HTTP keep-alive + бот). Локально: можно переопределить на bot.py
CMD ["python", "bot_replit.py"]
