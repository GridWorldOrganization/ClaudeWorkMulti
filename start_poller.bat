@echo off
chcp 65001 >nul
cd /d "%~dp0"
set AWS_DEFAULT_REGION=ap-northeast-1

REM config.env から環境変数を読み込み（コメント行・空行をスキップ）
if exist config.env (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("config.env") do (
        if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
    )
    echo config.env loaded.
) else (
    echo [WARN] config.env not found. Using defaults.
)

REM AWS認証確認
if not defined AWS_PROFILE (
    if not defined AWS_ACCESS_KEY_ID (
        echo [WARN] AWS_PROFILE も AWS_ACCESS_KEY_ID も未設定です
    )
)

python windows_poller.py
pause
