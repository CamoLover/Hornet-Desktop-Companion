@echo off
echo Building HornetDesktopCompanion.exe...

python -m pip install -r requirements.txt --quiet
python -m pip install pyinstaller --quiet

pyinstaller hornet-companion.spec --noconfirm

if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

if not exist release mkdir release
copy /y dist\HornetDesktopCompanion.exe release\
copy /y config.json release\

echo.
echo Done! Output is in the release\ folder.
pause
