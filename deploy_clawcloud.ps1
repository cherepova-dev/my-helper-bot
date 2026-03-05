# Пуш изменений для деплоя на Claw.Cloud
# Использование: .\deploy_clawcloud.ps1 ["сообщение коммита"]
# 1) Пушит в origin (GitHub). Если Claw.Cloud подключён к репозиторию — там подхватится.
# 2) Если добавлен remote "claw" — пушит и туда: git push claw main

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$msg = $args[0]
if (-not $msg) { $msg = "deploy: обновление бота v2 (парсинг, список задач)" }

git add bot_v2.py task_parsing.py db.py deploy_stamp.txt tests/
git add DEPLOY_CLAW_CLOUD.md deploy_clawcloud.ps1
git status -s

$count = (git status -s | Measure-Object -Line).Lines
if ($count -eq 0) {
    Write-Host "Нет изменений для коммита."
    exit 0
}

git commit -m $msg
git push origin main

$claw = git remote get-url claw 2>$null
if ($LASTEXITCODE -eq 0 -and $claw) {
    Write-Host "Пуш в remote claw..."
    git push claw main
}

Write-Host "Готово. Проверь деплой в панели Claw.Cloud Run."
