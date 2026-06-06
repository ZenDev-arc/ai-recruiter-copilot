@echo off
title Recruiter Copilot - Pipeline Scheduler
cd /d "%~dp0"
echo Starting pipeline scheduler (every 6 hours) ...
echo Press Ctrl+C to stop.
.venv\Scripts\python.exe scheduler.py --hours 6
pause
