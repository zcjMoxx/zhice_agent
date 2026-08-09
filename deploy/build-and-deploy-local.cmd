@echo off
setlocal

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0pipelines\build-and-deploy-local.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo Build and local deployment failed with exit code %EXIT_CODE%.
) else (
    echo Build and local deployment completed successfully.
)
pause
exit /b %EXIT_CODE%
