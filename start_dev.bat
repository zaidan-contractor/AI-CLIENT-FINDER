@echo off
setlocal enabledelayedexpansion

title Opportunity Platform - Development Launcher
echo =================================================================
echo   OPPORTUNITY PLATFORM - ONE-CLICK LOCAL DEV LAUNCHER
echo =================================================================
echo.

:: 1. Resolve Python executable (prefer backend venv if present)
set "PYTHON_EXE=python"
if exist "%~dp0backend\venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0backend\venv\Scripts\python.exe"
    echo [ENV] Using virtualenv Python: "%~dp0backend\venv\Scripts\python.exe"
) else (
    echo [ENV] Using system Python: python
)

:: 2. Check Docker Availability
echo.
echo [DOCKER] Checking Docker daemon status...
where docker >nul 2>&1
if errorlevel 1 (
    echo [WARN] Docker CLI not found. Assuming PostgreSQL and Redis are running locally.
) else (
    docker info >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Docker daemon not responding. Assuming PostgreSQL and Redis are running locally.
    ) else (
        echo [DOCKER] Docker Desktop is running. Ensuring Postgres and Redis containers are started...
        docker compose -f "%~dp0docker-compose.yml" up -d
    )
)

:: 3. Run Preflight Check (Verifies DB, Redis, Config & Schema)
echo.
echo [PREFLIGHT] Running environment preflight verification...
"%PYTHON_EXE%" "%~dp0scripts\dev_preflight.py"
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Preflight checks failed! Please review the messages above.
    pause
    exit /b 1
)

:: 4. Stop any existing stale processes on ports 8000 or 5500
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000,5500 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" >nul 2>&1

:: 5. Launch FastAPI Backend in separate visible window
echo.
echo [BACKEND] Starting FastAPI Backend on http://127.0.0.1:8000...
start "Opportunity Platform - Backend (Port 8000)" cmd /k "cd /d ""%~dp0backend"" && title Backend (8000) && ""%PYTHON_EXE%"" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

:: 6. Launch Static Frontend in separate visible window
echo [FRONTEND] Starting Static Frontend on http://127.0.0.1:5500...
start "Opportunity Platform - Frontend (Port 5500)" cmd /k "cd /d ""%~dp0frontend"" && title Frontend (5500) && ""%PYTHON_EXE%"" -m http.server 5500 --bind 127.0.0.1"

:: 7. Wait briefly for server startup
ping 127.0.0.1 -n 3 >nul 2>&1

:: 8. Print Ready Banner
echo.
echo =================================================================
echo   LOCAL DEVELOPMENT ENVIRONMENT IS READY!
echo =================================================================
echo.
echo   Frontend Application: http://127.0.0.1:5500
echo   FastAPI API Base:     http://127.0.0.1:8000/api/v1
echo   Interactive API Docs: http://127.0.0.1:8000/docs
echo.
echo   Default Demo Credentials:
echo     Email:    admin@omni.com
echo     Password: AdminPass123!
echo.
echo   Commands:
echo     seed_demo.bat - Seed or verify demo dataset
echo     stop_dev.bat  - Cleanly stop backend and frontend services
echo =================================================================
echo.

:: 9. Open browser automatically
start http://127.0.0.1:5500

endlocal
