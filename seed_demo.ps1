# Opportunity Platform - Seed Demo Data (PowerShell)
$rootPath = $PSScriptRoot
$venvPython = Join-Path $rootPath "backend\venv\Scripts\python.exe"

if (Test-Path $venvPython) {
    $pythonExe = $venvPython
} else {
    $pythonExe = "python"
}

$seedScript = Join-Path $rootPath "backend\scripts\seed_demo_data.py"
& $pythonExe $seedScript $args
