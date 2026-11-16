@echo off
REM LAB-Installer.bat - Launcher Kreatora WWW LAB (Windows)
setlocal
cd /d "%~dp0"

set "INIT_SCRIPT="
if exist "%~dp0lab-init.py" (
    set "INIT_SCRIPT=%~dp0lab-init.py"
) else if exist "%~dp0.lab-payload\lab-init.py" (
    set "INIT_SCRIPT=%~dp0.lab-payload\lab-init.py"
)
if "%INIT_SCRIPT%"=="" (
    echo BLAD: Nie znaleziono pliku lab-init.py ani w katalogu glownym, ani w .lab-payload\.
    pause
    exit /b 1
)

set "PY_CMD="
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if %errorlevel%==0 set "PY_CMD=py -3"
)

if "%PY_CMD%"=="" (
    where python >nul 2>&1
    if %errorlevel%==0 (
        python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
        if %errorlevel%==0 set "PY_CMD=python"
    )
)

if "%PY_CMD%"=="" (
    echo BLAD: Wymagany Python ^>= 3.11 ^(modul tomllib^).
    echo Pobierz Python 3.11+ ze strony https://www.python.org/downloads/
    pause
    exit /b 1
)

%PY_CMD% "%INIT_SCRIPT%" --web
if errorlevel 1 pause
