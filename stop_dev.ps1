# Opportunity Platform - Stop Development Services (PowerShell)
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  STOPPING LOCAL DEVELOPMENT SERVICES (PowerShell)" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

Get-NetTCPConnection -LocalPort 8000,5500 -ErrorAction SilentlyContinue | ForEach-Object {
    $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    if ($proc) {
        Write-Host "Stopping process $($proc.Id) ($($proc.ProcessName)) on port $($_.LocalPort)..." -ForegroundColor Yellow
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "[OK] Development web services on ports 8000 and 5500 stopped." -ForegroundColor Green
Write-Host "Note: PostgreSQL and Redis Docker containers remain running for data persistence."
Write-Host "To stop Docker containers: docker compose stop"
