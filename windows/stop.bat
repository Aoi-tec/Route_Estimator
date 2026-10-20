@echo off
call "%~dp0..\scripts\windows_entry.bat" stop %*
set "ROUTE_EXIT=%errorlevel%"
if not "%ROUTE_SETUP_NO_PAUSE%"=="1" pause
exit /b %ROUTE_EXIT%
