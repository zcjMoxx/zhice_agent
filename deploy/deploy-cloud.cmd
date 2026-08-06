@echo off
setlocal

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0pipelines\deploy-cloud.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo Full cloud deployment failed with exit code %EXIT_CODE%.
) else (
    echo Full cloud deployment completed successfully.
)
pause
exit /b %EXIT_CODE%
