@echo off
title Recruiter Copilot - Dashboard
cd /d "%~dp0"
echo Starting dashboard on http://localhost:5050 ...
.venv\Scripts\python.exe frontend\app.py
pause
