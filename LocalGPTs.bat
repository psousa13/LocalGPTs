@echo off
cd /d "%~dp0"
start "" "%~dp0localgpt\.venv\Scripts\pythonw.exe" "%~dp0launcher\launcher.py"
