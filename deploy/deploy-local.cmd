@echo off
setlocal

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0pipelines\deploy-local.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo Local deployment failed with exit code %EXIT_CODE%.
) else (
    echo Local deployment completed successfully.
)
pause
exit /b %EXIT_CODE%
