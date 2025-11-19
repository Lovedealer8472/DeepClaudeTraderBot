@echo off
echo Building UniRabbit.exe with PyInstaller...
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.11 and try again
    pause
    exit /b 1
)

REM Check if PyInstaller is installed
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Change to parent directory (where run.py is located)
cd ..

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist UniRabbit.spec del /q UniRabbit.spec

REM Copy spec file from release folder if it exists
if exist release\UniRabbit.spec (
    copy /y release\UniRabbit.spec UniRabbit.spec
    echo Using UniRabbit.spec for build...
)

REM Build with PyInstaller
echo.
echo Running PyInstaller...
if exist UniRabbit.spec (
    pyinstaller UniRabbit.spec
) else (
    pyinstaller --onefile --name UniRabbit --icon=NONE --console run.py
)

REM Check if build was successful
if not exist dist\UniRabbit.exe (
    echo ERROR: Build failed! UniRabbit.exe was not created.
    pause
    exit /b 1
)

REM Move exe to release folder
echo.
echo Moving UniRabbit.exe to release folder...
move /y dist\UniRabbit.exe release\UniRabbit.exe

REM Clean up build artifacts (keep spec in release folder)
echo.
echo Cleaning up build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist UniRabbit.spec del /q UniRabbit.spec

echo.
echo ========================================
echo Build complete! UniRabbit.exe is in release/
echo ========================================
pause

