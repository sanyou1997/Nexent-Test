@echo off
setlocal
cd /d "%~dp0" || exit /b 1
python -X utf8 -u "%~dp0deploy_windows.py" %*
exit /b %errorlevel%
