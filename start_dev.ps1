# Opportunity Platform - One-Click Local Development Launcher (PowerShell)
$Host.UI.RawUI.WindowTitle = "Opportunity Platform - Development Launcher"
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  OPPORTUNITY PLATFORM - ONE-CLICK LOCAL DEV LAUNCHER (PowerShell)" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host ""

$rootPath = $PSScriptRoot
$venvPython = Join-Path $rootPath "backend\venv\Scripts\python.exe"

if (Test-Path $venvPython) {
    $pythonExe = $venvPython
    Write-Host "[ENV] Using virtualenv Python: $pythonExe" -ForegroundColor Green
} else {
    $pythonExe = "python"
    Write-Host "[ENV] Using system Python: python" -ForegroundColor Yellow
}

# 1. Check Docker status
Write-Host ""
Write-Host "[DOCKER] Checking Docker daemon status..." -ForegroundColor Cyan
if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        $dockerInfo = docker info 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[DOCKER] Docker daemon is running. Ensuring PostgreSQL and Redis are started..." -ForegroundColor Green
            docker compose -f (Join-Path $rootPath "docker-compose.yml") up -d
        } else {
            Write-Host "[WARN] Docker daemon not responding. Assuming services are hosted in local/WSL." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "[WARN] Docker check error. Continuing..." -ForegroundColor Yellow
    }
} else {
    Write-Host "[WARN] Docker CLI not found. Assuming PostgreSQL and Redis are running locally." -ForegroundColor Yellow
}

# 2. Run Preflight
Write-Host ""
Write-Host "[PREFLIGHT] Running environment preflight verification..." -ForegroundColor Cyan
$preflightScript = Join-Path $rootPath "scripts\dev_preflight.py"
& $pythonExe $preflightScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Preflight checks failed! Please review errors above." -ForegroundColor Red
    exit 1
}

# 3. Clean up stale ports
Get-NetTCPConnection -LocalPort 8000,5500 -ErrorAction SilentlyContinue | ForEach-Object {
    $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Terminating stale process on port $($_.LocalPort) (PID $($proc.Id))..." -ForegroundColor Yellow
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

# 4. Launch Backend in new window
Write-Host ""
Write-Host "[BACKEND] Starting FastAPI Backend on http://127.0.0.1:8000..." -ForegroundColor Green
Start-Process -FilePath "cmd.exe" -ArgumentList "/k cd /d `"$rootPath\backend`" && title Backend (8000) && `"$pythonExe`" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

# 5. Launch Frontend in new window
Write-Host "[FRONTEND] Starting Static Frontend on http://127.0.0.1:5500..." -ForegroundColor Green
Start-Process -FilePath "cmd.exe" -ArgumentList "/k cd /d `"$rootPath\frontend`" && title Frontend (5500) && `"$pythonExe`" -m http.server 5500 --bind 127.0.0.1"

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  LOCAL DEVELOPMENT ENVIRONMENT IS READY!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  Frontend Application: http://127.0.0.1:5500" -ForegroundColor White
Write-Host "  FastAPI API Base:     http://127.0.0.1:8000/api/v1" -ForegroundColor White
Write-Host "  Interactive API Docs: http://127.0.0.1:8000/docs" -ForegroundColor White
Write-Host ""
Write-Host "  Default Demo Credentials:" -ForegroundColor Yellow
Write-Host "    Email:    admin@omni.com" -ForegroundColor Yellow
Write-Host "    Password: AdminPass123!" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Commands:" -ForegroundColor Cyan
Write-Host "    .\seed_demo.bat - Seed/verify demo dataset" -ForegroundColor White
Write-Host "    .\stop_dev.bat  - Stop development servers" -ForegroundColor White
Write-Host "=================================================================" -ForegroundColor Cyan

Start-Process "http://127.0.0.1:5500"
