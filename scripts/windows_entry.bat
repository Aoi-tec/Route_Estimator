@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
for %%I in ("%~dp0..") do set "ROUTE_ROOT=%%~fI"
cd /d "%ROUTE_ROOT%"
where py >nul 2>nul
if errorlevel 1 goto known_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto launch_py
:known_python
set "ROUTE_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%ROUTE_PYTHON%" goto check_known
set "ROUTE_PYTHON=%ProgramFiles%\Python312\python.exe"
if exist "%ROUTE_PYTHON%" goto check_known
where python >nul 2>nul
if errorlevel 1 goto install_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto launch_python
:install_python
where winget >nul 2>nul
if errorlevel 1 goto missing_winget
echo Installing Python 3.12 with Windows Package Manager. Follow any installer prompts.
winget install --id Python.Python.3.12 --exact --scope user
if errorlevel 1 goto install_failed
set "ROUTE_PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%ROUTE_PYTHON%" goto check_known
echo Python was installed. Close this window and run the BAT again to refresh PATH.
exit /b 2
:check_known
"%ROUTE_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if errorlevel 1 goto install_failed
"%ROUTE_PYTHON%" "%ROUTE_ROOT%\deploy\setup.py" %*
exit /b %errorlevel%
:launch_py
py -3 "%ROUTE_ROOT%\deploy\setup.py" %*
exit /b %errorlevel%
:launch_python
python "%ROUTE_ROOT%\deploy\setup.py" %*
exit /b %errorlevel%
:missing_winget
echo Python 3.12+ and winget are unavailable. Install Python from https://www.python.org/downloads/windows/
echo Then run this BAT again. Docker Desktop may also require WSL2, administrator approval, and a restart.
exit /b 2
:install_failed
echo Python installation failed or was cancelled. Existing project data was preserved.
exit /b 2
