@echo off
REM =============================================
REM AquaBeacon no-transmit (false-detection) test
REM   spec 19.1: no VALID must appear with no TX
REM   (ASCII + CRLF for reliable cmd.exe parsing)
REM =============================================

REM ========== set to match your hardware ==========
set RX_PORT=COM10
set MONITOR_SEC=15
REM ================================================

set LOG_DIR=logs
set TIMESTAMP=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%
set TIMESTAMP=%TIMESTAMP: =0%
set LOG_FILE=%LOG_DIR%\notx_%TIMESTAMP%.csv

if not exist %LOG_DIR% mkdir %LOG_DIR%

echo =============================================
echo  No-transmit (false-detection) test  RX: %RX_PORT%
echo =============================================
echo.
echo  Power off the TX board, or disconnect the SYNC/signal lines.
echo  (Pull RX inputs to GND if they would otherwise float.)
echo.
pause

echo.
echo Monitoring RX (%MONITOR_SEC%s)...
echo Saving to: %LOG_FILE%
python scripts\serial_monitor.py --port %RX_PORT% --duration %MONITOR_SEC% --out %LOG_FILE%
if errorlevel 1 (
    echo [ERROR] serial monitor failed
    exit /b 1
)

echo.
echo =============================================
echo  False-detection check
echo =============================================
python scripts\analyze_log.py %LOG_FILE% --no-tx

if errorlevel 1 (
    echo.
    echo [FAIL] VALID appeared with no TX - false detection
    exit /b 1
) else (
    echo.
    echo [PASS] no VALID with no TX
    exit /b 0
)
