# Деплой веб-интерфейса на Render (бесплатный тариф)

## Что сделано в коде

- **FastAPI** — `web/app.py`, маршруты: `/login`, `/today`, `/tasks`, `/reports/today`, `/reports/week`, POST добавление и отметка выполненного.
- **Та же БД** — через `DATABASE_URL` (PostgreSQL рекомендуется).
- **Кто ты в БД:** если в таблице `users` **ровно одна** запись (типичный случай), приложение **само** подключится к ней — **`WEB_INTERNAL_USER_ID` не обязателен**. Если пользователей несколько — задай `WEB_INTERNAL_USER_ID` вручную.

## Уже есть аккаунт Render и сервис бота

**Не создавай второй сервис**, если не хочешь два деплоя. Проще всего:

1. Открой [Render Dashboard](https://dashboard.render.com) → сервис **my-helper-bot** (или как он у тебя назван).
2. **Settings** → **Build & Deploy**:
   - **Start Command** замени на:  
     `uvicorn web.app:app --host 0.0.0.0 --port $PORT`  
     (раньше было `python bot_replit.py`).
3. **Environment** — **те же переменные**, что уже стоят: **`DATABASE_URL`**, `GROQ_API_KEY`, при необходимости токен Telegram (веб его не использует, можно оставить). Добавь только новые:
   - `WEB_APP_PASSWORD` — пароль входа на сайт;
   - `WEB_SESSION_SECRET` — случайная длинная строка (32+ символов).
4. **Manual Deploy** → **Deploy latest commit** (после пуша в репозиторий).

Так **подключение к БД не меняется** — ты просто переиспользуешь уже сохранённый `DATABASE_URL` из того же сервиса.

## Если создаёшь сервис с нуля

1. [render.com](https://render.com) → **New → Web Service** → репозиторий GitHub.
2. **Build Command:** `pip install -r requirements-render.txt`  
   **Start Command:** `uvicorn web.app:app --host 0.0.0.0 --port $PORT`  
   **Instance type:** Free

3. **Environment (обязательно):**

   | Переменная | Описание |
   |------------|----------|
   | `DATABASE_URL` | **Скопируй ту же строку**, что у старого бота / из Supabase (одна база — одни данные). |
   | `WEB_APP_PASSWORD` | Пароль для входа на сайт. |
   | `WEB_SESSION_SECRET` | Длинная случайная строка (32+ символов), для подписи cookie-сессии. |
   | `WEB_INTERNAL_USER_ID` | **Только если** в таблице `users` больше одной строки — укажи нужный `id`. |

4. Опционально: `GROQ_API_KEY` — для голоса на сервере позже.

5. **Deploy.** URL вида `https://my-helper-bot.onrender.com` → `/login` → **«Сегодня»**.

## Локальный запуск

```powershell
cd c:\Project\bot
$env:DATABASE_URL = "..."   # или SQLite: не задавать, задать BOT_DB_PATH
$env:WEB_APP_PASSWORD = "test"
$env:WEB_SESSION_SECRET = "32-символьная-случайная-строка"
$env:WEB_INTERNAL_USER_ID = "1"   # если нужен существующий user
uvicorn web.app:app --reload --host 127.0.0.1 --port 8080
```

Открыть: http://127.0.0.1:8080

## Healthcheck

`GET /health` — для Render и для UptimeRobot.
