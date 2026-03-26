# Пуш изменений для деплоя на Claw.Cloud
# Использование: .\deploy_clawcloud.ps1 ["сообщение коммита"]
# 1) Пушит в origin (GitHub). Если Claw.Cloud подключён к репозиторию — там подхватится.
# 2) Если добавлен remote "claw" — пушит и туда: git push claw main

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$msg = $args[0]
if (-not $msg) { $msg = "v2: parsing, task list, claw.cloud deploy" }

# Файлы бота v2 и тестов
git add bot_v2.py task_parsing.py deploy_stamp.txt tests/test_task_parsing.py tests/test_bot_v2.py
git add DEPLOY_CLAW_CLOUD.md deploy_clawcloud.ps1
git status -s

$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "Нет изменений для коммита. Сначала внеси правки или добавь файлы (git add)."
    exit 0
}

git commit -m $msg
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

git push origin main
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Автообновление актуального образа в DEPLOY_CLAW_CLOUD.md
$fullSha = (git rev-parse HEAD).Trim()
$image = "yuliacherepova/my-helper-bot:$fullSha"
$deployDoc = Join-Path $PSScriptRoot "DEPLOY_CLAW_CLOUD.md"

if (Test-Path $deployDoc) {
    $content = Get-Content -Path $deployDoc -Raw -Encoding UTF8
    $pattern = "Актуальный образ: `yuliacherepova/my-helper-bot:[0-9a-f]{40}`"
    $replacement = "Актуальный образ: ``$image``"
    if ($content -match $pattern) {
        $newContent = [Regex]::Replace($content, $pattern, $replacement, 1)
    } else {
        $anchor = "3. В поле **Image** укажи полное имя образа с тегом по коммиту:"
        if ($content.Contains($anchor)) {
            $newContent = $content.Replace(
                $anchor,
                "$anchor`r`n   Актуальный образ: ``$image``"
            )
        } else {
            $newContent = $content
        }
    }

    if ($newContent -ne $content) {
        Set-Content -Path $deployDoc -Value $newContent -Encoding UTF8
        git add DEPLOY_CLAW_CLOUD.md
        $docCommitMsg = "docs(claw): update current production image tag"
        git commit -m $docCommitMsg
        if ($LASTEXITCODE -eq 0) {
            git push origin main
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
    }
}

try {
    $clawUrl = git remote get-url claw 2>$null
    if ($clawUrl) {
        Write-Host "Push to claw..."
        git push claw main
    }
} catch {}

Write-Host "Done. Check deploy at Claw.Cloud Run."
Write-Host "Current image: $image"
