@echo off
echo Creating UniRabbit-Package.zip...
echo.

REM Change to parent directory
cd ..

REM Remove old zip if it exists
if exist UniRabbit-Package.zip del /q UniRabbit-Package.zip

REM Create zip file (requires PowerShell)
powershell -Command "Compress-Archive -Path release\* -DestinationPath UniRabbit-Package.zip -Force"

if exist UniRabbit-Package.zip (
    echo.
    echo ========================================
    echo Package created: UniRabbit-Package.zip
    echo ========================================
) else (
    echo ERROR: Failed to create zip file
    pause
    exit /b 1
)

pause

