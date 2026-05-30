@echo off
cd /d "%~dp0"

set VENV=.venv

if not exist "%VENV%\Scripts\activate.bat" (
    echo Creating virtual environment...
    python -m venv %VENV%
)

call %VENV%\Scripts\activate.bat

echo Installing requirements...
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo Launching Hornet...
start /b pythonw companion.py
