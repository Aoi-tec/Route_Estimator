@echo off
call "%~dp0scripts\windows_entry.bat" doctor %*
set "ROUTE_EXIT=%errorlevel%"
if not "%ROUTE_SETUP_NO_PAUSE%"=="1" pause
exit /b %ROUTE_EXIT%
