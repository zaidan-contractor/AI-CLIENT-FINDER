@echo off
setlocal

title Opportunity Platform - Stop Development Services
echo =================================================================
echo   STOPPING LOCAL DEVELOPMENT SERVICES
echo =================================================================
echo.

echo [1/2] Terminating processes listening on port 8000 (Backend) and port 5500 (Frontend)...
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000,5500 -ErrorAction SilentlyContinue | ForEach-Object { $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; if ($proc) { Write-Host '  Stopping process:' $proc.Id ($proc.ProcessName); Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } }"

echo.
echo [2/2] Checking development containers...
echo Note: PostgreSQL and Redis containers remain running for data persistence.
echo If you wish to also stop the Docker containers, run:
echo   docker compose stop
echo.
echo [OK] Development web services stopped successfully.
echo.

endlocal
