@echo off
setlocal
cd /d "%~dp0"
python "%~dp0adc_waveform_viewer.py"
if errorlevel 1 pause
