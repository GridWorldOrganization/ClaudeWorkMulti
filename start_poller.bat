@echo off
cd /d "%~dp0"
set AWS_PROFILE=chatwork-webhook
set AWS_DEFAULT_REGION=ap-northeast-1
python windows_poller.py
pause
