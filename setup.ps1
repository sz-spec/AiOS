# VOS3 Project Setup Script
# ===========================
# Run with: .\setup.ps1

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  VOS3 - AI Operating System Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
Write-Host "[1/6] Checking Python..." -ForegroundColor Yellow
$pythonVersion = python --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: $pythonVersion" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Python not found. Install Python 3.11+" -ForegroundColor Red
    exit 1
}

# Check Node.js
Write-Host "[2/6] Checking Node.js..." -ForegroundColor Yellow
$nodeVersion = node --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK: Node.js $nodeVersion" -ForegroundColor Green
} else {
    Write-Host "  ERROR: Node.js not found. Install Node.js 18+" -ForegroundColor Red
    exit 1
}

# Create .env if not exists
Write-Host "[3/6] Setting up environment..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env" -ErrorAction SilentlyContinue
    if (Test-Path ".env") {
        Write-Host "  Created .env from .env.example" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: No .env.example found, skipping" -ForegroundColor Yellow
    }
} else {
    Write-Host "  OK: .env already exists" -ForegroundColor Green
}

# Install backend dependencies
Write-Host "[4/6] Installing backend dependencies..." -ForegroundColor Yellow
Push-Location backend
if (Test-Path "..\requirements_win.txt") {
    pip install -r ..\requirements_win.txt --quiet
} elseif (Test-Path "..\requirements.txt") {
    pip install -r ..\requirements.txt --quiet
}
Write-Host "  OK: Backend dependencies installed" -ForegroundColor Green
Pop-Location

# Install frontend dependencies
Write-Host "[5/6] Installing frontend dependencies..." -ForegroundColor Yellow
Push-Location frontend
npm install --silent 2>&1 | Out-Null
Write-Host "  OK: Frontend dependencies installed" -ForegroundColor Green
Pop-Location

# Create data directories
Write-Host "[6/6] Creating data directories..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path "data/memory" -Force | Out-Null
New-Item -ItemType Directory -Path "data/cache" -Force | Out-Null
Write-Host "  OK: Data directories created" -ForegroundColor Green

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start the servers:" -ForegroundColor White
Write-Host "  Backend:  cd backend && uvicorn main:app --reload --port 8000" -ForegroundColor Gray
Write-Host "  Frontend: cd frontend && npm run dev" -ForegroundColor Gray
Write-Host ""
Write-Host "Or run both:" -ForegroundColor White
Write-Host "  .\run.ps1" -ForegroundColor Gray
Write-Host ""
