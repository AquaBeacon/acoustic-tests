@echo off
REM =============================================
REM AquaBeacon test loop
REM   build -> flash -> monitor -> analyze -> pass/fail
REM   (ASCII + CRLF for reliable cmd.exe parsing)
REM =============================================

REM ========== set to match your hardware ==========
set TX_PORT=COM5
set RX_PORT=COM10
set MONITOR_SEC=30
REM ================================================

set LOG_DIR=logs
set TIMESTAMP=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOG_FILE=%LOG_DIR%\rx_%TIMESTAMP%.csv

if not exist %LOG_DIR% mkdir %LOG_DIR%

echo =============================================
echo  AquaBeacon test loop
echo  TX: %TX_PORT%  RX: %RX_PORT%
echo =============================================

echo.
echo [1/5] Building TX...
cd tx
call pio run
if errorlevel 1 (
    echo [ERROR] TX build failed
    cd ..
    exit /b 1
)
cd ..

echo.
echo [2/5] Building RX...
cd rx
call pio run
if errorlevel 1 (
    echo [ERROR] RX build failed
    cd ..
    exit /b 1
)
cd ..

echo.
echo [3/5] Flashing TX (%TX_PORT%)...
cd tx
call pio run -t upload --upload-port %TX_PORT%
if errorlevel 1 (
    echo [ERROR] TX upload failed - check %TX_PORT%
    cd ..
    exit /b 1
)
cd ..

echo.
echo [4/5] Flashing RX (%RX_PORT%)...
cd rx
call pio run -t upload --upload-port %RX_PORT%
if errorlevel 1 (
    echo [ERROR] RX upload failed - check %RX_PORT%
    cd ..
    exit /b 1
)
cd ..

echo.
echo Waiting for Pico USB re-enumeration (5s)...
ping -n 6 127.0.0.1 > nul

echo.
echo [5/5] Monitoring RX (%MONITOR_SEC%s)...
echo Saving to: %LOG_FILE%
python scripts\serial_monitor.py --port %RX_PORT% --duration %MONITOR_SEC% --out %LOG_FILE%
if errorlevel 1 (
    echo [ERROR] serial monitor failed
    exit /b 1
)

echo.
echo =============================================
echo  Log analysis
echo =============================================
python scripts\analyze_log.py %LOG_FILE%

if errorlevel 1 (
    echo.
    echo [FAIL] test failed - ask Claude Code to fix
    exit /b 1
) else (
    echo.
    echo [PASS] test passed
    exit /b 0
)
