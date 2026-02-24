# Запуск деплоя на Render по Deploy Hook.
# В .env задай: RENDER_DEPLOY_HOOK_URL=https://api.render.com/deploy/srv-xxxxx?key=yyyyy
# Или передай URL первым аргументом: .\trigger_render_deploy.ps1 "https://api.render.com/..."

$url = $args[0]
if (-not $url) {
    if (Test-Path .env) {
        Get-Content .env | ForEach-Object {
            if ($_ -match '^\s*RENDER_DEPLOY_HOOK_URL=(.+)$') { $url = $matches[1].Trim() }
        }
    }
}
if (-not $url) {
    Write-Host "Usage: .\trigger_render_deploy.ps1 <RENDER_DEPLOY_HOOK_URL>"
    Write-Host "Or set RENDER_DEPLOY_HOOK_URL in .env"
    exit 1
}
try {
    $r = Invoke-WebRequest -Uri $url -Method Get -UseBasicParsing
    if ($r.StatusCode -eq 200) { Write-Host "Deploy triggered." } else { Write-Host "Response: $($r.StatusCode)" }
} catch {
    Write-Host "Error: $_"
    exit 1
}
