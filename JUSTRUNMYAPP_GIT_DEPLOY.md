# Деплой бота и веба на JustRunMy.App

Площадка: **JustRunMy.App**, приложение **interesting_carver** (account `a7947_f6QW`).

**Сборка контейнера — облачная.** Локальный Docker Desktop **не нужен**. Всё, что требуется — `git push`. Платформа сама принимает код, собирает Docker-образ из `Dockerfile` в репозитории и перезапускает контейнер.

Код одновременно живёт на:

- **GitHub** — `origin` (`https://github.com/cherepova-dev/my-helper-bot.git`), ветка `main`.
- **JustRunMy.App** — `justrunmyapp` (`https://justrunmy.app/git/a7947_f6QW`), сборку триггерит push в ветку `deploy`.

---

## Стандартный деплой (как делаем сейчас)

В папке проекта (`c:\Project\bot`) выполнить:

```powershell
git push origin main
git push justrunmyapp main:deploy
```

Что делает второй пуш:

1. Отправляет `main` в ветку `deploy` на стороне JustRunMy.
2. Платформа печатает в ответ `Starting image build` — облачный билдер берётся за `Dockerfile`.
3. После успешной сборки в панели приложения **Deployment → Docker Image** появляется новый образ и контейнер автоматически перезапускается с обновлённым кодом и фронтом (`web/static/app.js`, `style.css`, шаблоны).

Скрипт-обёртка, делает то же самое:

```powershell
.\deploy_justrunmyapp.ps1
```

---

## Что обязательно делать перед пушем

1. Закоммитить изменения локально (`git add ... && git commit -m ...`).
2. Прогнать тесты, если правились бизнес-файлы:
   ```powershell
   python -m pytest tests/ -q
   ```
3. **Не** трогать ветку `deploy` руками: всегда пушим `main:deploy`, тогда `main` остаётся источником правды.

---

## Если в ответе на пуш приходит `No matching nodes (preffer node not found)`

Это **инфраструктурная ошибка JustRunMy** (нет свободной ноды для облачного билдера), не наш код. Признаки:

```
remote: Starting image build
remote: !! Error processing git push:
remote: !! No matching nodes(preffer node not found)
```

Что важно знать:

- **Push в ветку `deploy` всё равно проходит** — обновление кода ставится в очередь.
- Платформа **может собрать образ позже сама**, когда нода освободится. Несколько раз так и случалось: спустя время фронт в проде обновлялся без дополнительных действий.
- Образ может также собраться после **Restart** приложения в панели (если приложение было «stale / inactive 30 days»), но именно при условии, что в очереди есть необработанный успешный пуш.

Что делать:

1. **Не паниковать**, не пытаться форсировать локальный Docker.
2. Через 5–30 минут открыть в панели:
   - **Applications → interesting_carver → Deployment** — должен обновиться **Docker Image** (новая дата/тег).
   - **Logs / Diagnostics** — должен появиться свежий запуск (`uvicorn ... running on ...` или `DEPLOY_STAMP: ...`).
3. В браузере на проде нажать **Ctrl+F5**, чтобы подтянулись свежие `static/style.css` и `app.js`.
4. Если образ долго не появляется — повторить `git push justrunmyapp main:deploy`. Часто следующая попытка проходит успешно.
5. Если стабильно падает несколько раз подряд — открыть в панели **Notifications / Help** и написать тикет с дословным текстом ошибки и именем приложения (`interesting_carver`, account `a7947_f6QW`).

---

## Резервный путь: Zip-аплоад (тоже облачная сборка, без Docker)

Используется, **только если** Git-пуш стабильно падает и поддержка не отвечает. Локально Docker всё равно не нужен — JustRunMy сама соберёт образ из архива.

1. Пересобрать архив из текущего коммита:
   ```powershell
   .\make_deploy_zip.ps1
   ```
   Появится `c:\Project\bot\bot-deploy.zip` (внутри весь репозиторий, включая `web/static`, `web/templates`, `Dockerfile`).
2. В панели открыть **Applications → interesting_carver → Deployment** и в секции **Zip / Upload Code** загрузить `bot-deploy.zip`.
3. Подождать сборку — после неё в **Docker Image** появится новый образ, контейнер перезапустится сам.

---

## Чего НЕ делаем

- **Не ставим Docker Desktop** и не выполняем `docker login / docker build / docker push` руками. Это путь «Docker Push Deployment», он у нас **не используется**. В панели может висеть надпись «Docker image is not set» — это нормально, пока хотя бы одна сборка не прошла; собирает её JustRunMy в облаке после нашего git push.
- **Не нажимаем «Change or set docker image»** вручную, если своих образов в их registry мы не пушили — там всё равно будет пусто.
- **Не меняем ветку**: пушим только `main:deploy`.

---

## Однократная настройка (если приложение пересоздаётся с нуля)

1. В JustRunMy.App: **Create application** → способ деплоя **Git Push** (не Zip, не Docker Push).
2. Скопировать в панели **Git remote URL** вида `https://justrunmy.app/git/<id>`.
3. В репозитории добавить remote (один раз):
   ```powershell
   git remote add justrunmyapp <URL_ИЗ_ПАНЕЛИ>
   ```
   Если уже есть, но нужно обновить URL:
   ```powershell
   git remote set-url justrunmyapp <НОВЫЙ_URL>
   ```
4. Проверить:
   ```powershell
   git remote -v
   ```
   Должны быть `origin` (GitHub) и `justrunmyapp` (JustRunMy.App).
5. В **Settings** приложения проверить переменные окружения:
   - `DATABASE_URL`
   - `WEB_APP_PASSWORD`
   - `WEB_SESSION_SECRET`
   - `TELEGRAM_BOT_TOKEN`
   - `GROQ_API_KEY` (для голоса)
6. Если нужен веб-интерфейс, в **Run command** указать:
   ```text
   uvicorn web.app:app --host 0.0.0.0 --port $PORT
   ```
   Если поле пустое, JustRunMy возьмёт `CMD` из `Dockerfile` (`python bot_replit.py` — только бот, без сайта).

---

## Кратко (шпаргалка)

| Действие | Команда / место |
|---|---|
| Стандартный деплой (бот + веб) | `git push origin main` → `git push justrunmyapp main:deploy` |
| То же одной кнопкой | `.\deploy_justrunmyapp.ps1` |
| Проверить, что всё подцепилось | панель → **Deployment** (новая дата у Docker Image) и **Logs** |
| Обновить страницу в браузере на проде | **Ctrl+F5** |
| Если ответ `No matching nodes` | подождать 5–30 минут, повторить пуш, при необходимости — тикет в поддержку |
| Резерв (без Docker) | `.\make_deploy_zip.ps1` → загрузить `bot-deploy.zip` в панель |
| Однократная привязка remote | `git remote add justrunmyapp <URL>` |

**Главное:** наш Docker — облачный, на стороне JustRunMy. Локально Docker не ставим, ничего вручную в их registry не пушим. Достаточно `git push justrunmyapp main:deploy`.
