# Подключение Render по Git и деплой бота

Чтобы Render сам подхватывал код из репозитория и деплоил бота, достаточно один раз подключить GitHub и ввести токен.

---

## Шаг 1: Репозиторий на GitHub

1. Зайди на **https://github.com** и залогинься.
2. Нажми **+** → **New repository**.
3. Имя, например: `my-helper-bot`. Public. **Create repository**.
4. На своём компе в папке `c:\Project\bot` выполни (если ещё не ставил Git — установи с https://git-scm.com):

```bash
cd c:\Project\bot
git init
git add bot.py bot_replit.py requirements.txt render.yaml
git commit -m "Add bot and Render blueprint"
git branch -M main
git remote add origin https://github.com/ТВОЙ_ЛОГИН/my-helper-bot.git
git push -u origin main
```

Подставь свой логин GitHub и имя репо. Если Git спросит логин/пароль — используй **Personal Access Token** (GitHub → Settings → Developer settings → Personal access tokens) вместо пароля.

---

## Шаг 2: Подключить репо к Render (Blueprint)

1. Зайди на **https://dashboard.render.com** и залогинься.
2. Нажми **New +** → **Blueprint**.
3. **Connect a repository** — выбери **GitHub** и разреши доступ к репозиториям (или только к `my-helper-bot`).
4. В списке репозиториев выбери **my-helper-bot** (или как назвал) и нажми **Connect**.
5. Render прочитает `render.yaml` и покажет, что будет создан сервис **my-helper-bot**. Нажми **Apply**.
6. В карточке сервиса открой **Environment** и добавь переменную:
   - **Key:** `TELEGRAM_BOT_TOKEN`
   - **Value:** твой токен (например `8785603117:AAGWVVEWSVbIc_ZZDhd26OprknT0e6Ldh1Q`)
7. Сохрани. Render сам запустит деплой (или нажми **Manual Deploy** → **Deploy latest commit**).

После успешного деплоя бот будет доступен по ссылке вида `https://my-helper-bot.onrender.com`. Для работы бота 24/7 настрой пинг в UptimeRobot по этой ссылке (см. **DEPLOY_24_7.md**, раздел Render, п. 5).

---

## Запуск деплоя по API (по желанию)

Когда сервис уже создан в Render:

1. В Dashboard открой свой сервис → **Settings** → внизу **Deploy Hook**.
2. Скопируй URL (вида `https://api.render.com/deploy/srv-xxxxx?key=yyyyy`).
3. Сохрани его в файл `.env` в папке бота:  
   `RENDER_DEPLOY_HOOK_URL=https://api.render.com/deploy/srv-xxxxx?key=yyyyy`  
   (или запускай скрипт с этим URL).
4. Запуск деплоя с твоего компа:
   ```powershell
   cd c:\Project\bot
   powershell -ExecutionPolicy Bypass -File trigger_render_deploy.ps1
   ```
   Скрипт отправит запрос по этому URL — Render начнёт новый деплой с последнего коммита в GitHub.

---

## Дальше: обновления кода

Как только ты делаешь `git push` в этот репозиторий, Render по умолчанию сам запускает новый деплой. Ничего вручную в Render делать не нужно — только пушить в GitHub.

---

## Если репо уже подключён к Render

Если ты уже создала Web Service вручную и подключила репо:

1. Добавь в корень репо файл **render.yaml** (он уже есть в `c:\Project\bot`) и запушь.
2. В Render в настройках сервиса проверь:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot_replit.py`
   - В **Environment** есть **TELEGRAM_BOT_TOKEN**.

Либо удали старый сервис и создай новый через **Blueprint** (New + → Blueprint → тот же репо) — тогда всё возьмётся из `render.yaml`.
