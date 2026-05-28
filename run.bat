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

if "%1"=="--force" goto extract
if not exist "assets\sprites\idle\hornet_idle.png" goto extract
if not exist "assets\sprites\fast_fall\hornet_fast_fall.png" goto extract
if not exist "assets\sprites\fast_fall\hornet_fast_fall_wrong.png" goto extract
if not exist "assets\sprites\sit\hornet_sit_0.png" goto extract
echo Sprites already extracted. (Pass --force to re-extract)
goto launch

:extract
echo Extracting sprites...
python extract_sprite.py %1

:launch
echo Launching Hornet...
start /b pythonw companion.py
