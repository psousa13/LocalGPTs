@echo off
cd /d "%~dp0"
"%~dp0localgpt\.venv\Scripts\python.exe" "%~dp0launcher\launcher.py"
pause
