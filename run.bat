@echo off
REM Quick activation and run script for Windows
REM Usage: run.bat

cd /d "%~dp0"
call venv\Scripts\activate.bat
python src\main.py
pause
