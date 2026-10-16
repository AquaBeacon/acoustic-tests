@echo off
REM =============================================
REM Flash the continuous 200kHz wave program (wave/ env:wave) and monitor.
REM   Outputs a 50%% duty 200kHz square wave on GP15, forever.
REM   NOTE: this overwrites whatever firmware is on WAVE_PORT
REM         (e.g. the TX test firmware). Re-flash later via run_test.bat.
REM =============================================

REM ========== set to the board you want to drive ==========
set WAVE_PORT=COM5
REM ========================================================

echo Building + flashing 200kHz continuous wave to %WAVE_PORT% ...
pio run -d wave -e wave -t upload --upload-port %WAVE_PORT%
if errorlevel 1 (
    echo [ERROR] upload failed - check %WAVE_PORT%
    exit /b 1
)

echo.
echo Wave is now running on GP15. Opening monitor (Ctrl+C to stop)...
ping -n 3 127.0.0.1 > nul
pio device monitor --port %WAVE_PORT% --baud 115200
