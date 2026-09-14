@echo off
cd /d "%~dp0"

title Markdown TTS - MOSS

echo.
echo ========================================
echo Markdown TTS - MOSS Local v1.5
echo ========================================
echo.
echo Iniciando aplicacao local...
echo.

".venv\Scripts\python.exe" web.py

pause