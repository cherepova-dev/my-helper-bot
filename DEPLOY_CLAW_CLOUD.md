# Деплой бота на Claw.Cloud (ClawCloud Run)

[ClawCloud Run](https://run.claw.cloud/) — платформа для запуска приложений из Docker-образов. Бот деплоится так же, как при первой сборке v2: **push в GitHub → сборка образа в GitHub Actions → в Claw.Cloud указываешь новый Image**.

## Как запушовать изменения (как делали при сборке v2)

### Шаг 1: Пуш кода в GitHub

В папке проекта:

```powershell
cd c:\Project\bot
git add -A
git status
git commit -m "v2: описание изменений"
git push origin main
```

Или через скрипт (закоммитит и запушит указанные файлы):

```powershell
.\deploy_clawcloud.ps1 "v2: обновление"
```

### Шаг 2: Дождаться сборки образа

После пуша в ветку `main` срабатывает GitHub Actions (репозиторий `cherepova-dev/my-helper-bot`): собирается Docker-образ и пушится в Docker Hub.

- Зайди в репозиторий на GitHub → вкладка **Actions**.
- Дождись зелёного завершения workflow **Build and Push Docker Image**.
- В run открой шаг "Build and push" или итог — там будет использован коммит. Или скопируй **полный SHA коммита** (40 символов) со страницы коммита на GitHub (например `d39875ffede09b46f795e529294c27d41d0c4e66`).

### Шаг 3: Указать новый Image в Claw.Cloud

1. Открой [Claw.Cloud Run](https://run.claw.cloud/) → своё приложение (например **my-helper-bot**).
2. Зайди в настройки приложения (Settings / Image / Deployment).
3. В поле **Image** укажи полное имя образа с тегом по коммиту:
   ```text
   yuliacherepova/my-helper-bot:<полный_sha_коммита>
   ```
   Актуальный образ: `yuliacherepova/my-helper-bot:6d1d3289c4f43c9dec23553d87bc518d7b1761c8`  
   Пример: `yuliacherepova/my-helper-bot:d39875ffede09b46f795e529294c27d41d0c4e66`  
   Либо можно использовать `yuliacherepova/my-helper-bot:latest` (образ от последнего пуша в `main`).
4. Сохрани настройки и сделай **Restart** приложения.

После рестарта в логах должна появиться строка с `DEPLOY_STAMP` и версией бота (v2).

---

### Кратко: что делать при каждом обновлении

1. `git push origin main` (или `.\deploy_clawcloud.ps1`).
2. Дождаться успешной сборки в GitHub Actions.
3. В Claw.Cloud в поле **Image** указать `yuliacherepova/my-helper-bot:<sha>` или `yuliacherepova/my-helper-bot:latest`, сохранить и нажать **Restart**.

### Вариант 2: Деплой из Docker-образа

1. Соберите образ (локально или через CI):
   ```bash
   docker build -t your-registry/helper-bot:latest .
   docker push your-registry/helper-bot:latest
   ```
2. В [ClawCloud Run](https://run.claw.cloud/) → **App Launchpad** → **Create App**.
3. Укажите образ (публичный или из приватного реестра), порт **8080**, команду запуска: `python bot_replit.py`.
4. В **Advanced** задайте переменные: `TELEGRAM_BOT_TOKEN`, при необходимости `DATABASE_URL`, `PORT=8080`.

### Переменные окружения

- `TELEGRAM_BOT_TOKEN` — токен бота (обязательно).
- `PORT` — порт HTTP для keep-alive (по умолчанию 8080).
- `DATABASE_URL` — если используется БД.

### Проверка после деплоя

В логах приложения должна появиться строка:
`DEPLOY_STAMP: 20260223-v2-parsing-list-human-date (bot v2)`
