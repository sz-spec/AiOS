# VOS3 Run Script
# ================
# Starts both backend and frontend servers

Write-Host "Starting VOS3..." -ForegroundColor Cyan

# Start backend in background
Write-Host "Starting backend on http://localhost:8000" -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd backend; uvicorn main:app --reload --port 8000"

# Wait for backend to start
Start-Sleep -Seconds 3

# Start frontend in background
Write-Host "Starting frontend on http://localhost:3000" -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd frontend; npm run dev"

# Wait for frontend to start
Start-Sleep -Seconds 5

# Open browser
Write-Host "Opening browser..." -ForegroundColor Green
Start-Process "http://localhost:3000"

Write-Host ""
Write-Host "VOS3 is running!" -ForegroundColor Green
Write-Host "  Backend:  http://localhost:8000" -ForegroundColor Gray
Write-Host "  Frontend: http://localhost:3000" -ForegroundColor Gray
Write-Host "  API Docs: http://localhost:8000/docs" -ForegroundColor Gray
