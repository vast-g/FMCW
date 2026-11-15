@echo off
cd /d "C:\Users\Lenovo\Desktop\new_rag"

echo ========================================
echo   Slope Monitor - Quick Start
echo ========================================
echo.
echo   [1] GUI Mode (window + camera)
echo   [2] API Mode (http://127.0.0.1:8000/docs)
echo.
set /p mode="Choose (1/2): "

echo.
echo [*] Starting sensor simulator...
start "SensorSim" python sensor_simulator.py
timeout /t 2 /nobreak >nul

if "%mode%"=="2" (
    echo [*] Starting API mode...
    python end_new.py --api
) else (
    echo [*] Starting GUI mode...
    python end_new.py
)
