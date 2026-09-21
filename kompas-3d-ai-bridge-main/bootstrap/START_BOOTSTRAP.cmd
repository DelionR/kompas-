@echo off
title KOMPAS CODEX BOOTSTRAP
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "C:\KOMPAS_AI_BRIDGE\bootstrap\START_BOOTSTRAP_SAFE.ps1" -Root "C:\KOMPAS_AI_BRIDGE"
if errorlevel 1 (
  echo.
  echo Bootstrap did not start. Read the error above.
  pause
)
