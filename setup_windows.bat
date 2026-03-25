@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Chatwork Webhook Poller セットアップ ===
echo.

REM config.env の存在確認
if not exist "config.env" (
    echo [ERROR] config.env が見つかりません
    echo config.env.example をコピーして config.env を作成してください
    pause
    exit /b 1
)

REM config.env から環境変数を読み込む
for /f "usebackq tokens=1,* delims==" %%a in ("config.env") do (
    echo %%a | findstr /r "^#" >nul || (
        if not "%%a"=="" set "%%a=%%b"
    )
)
echo config.env loaded.
echo.

REM ===== Step 1: Python確認 =====
echo [1/4] Python 確認中...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python がインストールされていません
    echo.
    echo   以下からインストールしてください:
    echo   https://www.python.org/downloads/
    echo.
    echo   インストール時に「Add Python to PATH」にチェックを入れること
    echo.
    pause
    exit /b 1
)
python --version
echo   OK
echo.

REM ===== Step 2: pip パッケージインストール =====
echo [2/4] boto3, requests インストール中...
pip install boto3 requests
echo   OK
echo.

REM ===== Step 3: AWS CLI 確認 + インストール案内 =====
echo [3/4] AWS CLI 確認中...
aws --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] AWS CLI がインストールされていません
    echo.
    echo   以下の手順でインストールしてください:
    echo.
    echo   1. ブラウザで以下を開く:
    echo      https://awscli.amazonaws.com/AWSCLIV2.msi
    echo.
    echo   2. ダウンロードした AWSCLIV2.msi をダブルクリックしてインストール
    echo.
    echo   3. インストール完了後、このコマンドプロンプトを閉じて、
    echo      setup_windows.bat をもう一度実行してください
    echo.
    pause
    exit /b 1
)
aws --version
echo   OK
echo.

REM ===== Step 4: AWS プロファイル設定 =====
echo [4/4] AWS プロファイル「chatwork-webhook」を設定中...
aws configure set aws_access_key_id %AWS_ACCESS_KEY_ID% --profile chatwork-webhook
aws configure set aws_secret_access_key %AWS_SECRET_ACCESS_KEY% --profile chatwork-webhook
aws configure set region ap-northeast-1 --profile chatwork-webhook
echo   OK
echo.

echo === セットアップ完了 ===
echo.
echo 起動方法: start_poller.bat をダブルクリック
echo.
pause
