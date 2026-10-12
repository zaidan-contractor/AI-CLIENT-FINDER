@echo off
setlocal

title Opportunity Platform - Seed Demo Data
echo =================================================================
echo   SEEDING / VERIFYING DEMO DATASET
echo =================================================================
echo.

set "PYTHON_EXE=python"
if exist "%~dp0backend\venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0backend\venv\Scripts\python.exe"
)

echo Executing: "%PYTHON_EXE%" "%~dp0backend\scripts\seed_demo_data.py" %*
"%PYTHON_EXE%" "%~dp0backend\scripts\seed_demo_data.py" %*

echo.
echo =================================================================
echo   DEMO ACCOUNT READY:
echo     Email:    admin@omni.com
echo     Password: AdminPass123!
echo     Workspaces: ws_demo_1, ws_demo_2 (Org: org_demo)
echo =================================================================
echo.

endlocal
