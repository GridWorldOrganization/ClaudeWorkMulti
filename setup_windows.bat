@echo off
echo === Chatwork Webhook Poller セットアップ ===
echo.

REM Python確認
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python がインストールされていません
    echo https://www.python.org/downloads/ からインストールしてください
    pause
    exit /b 1
)

REM 必要パッケージインストール
echo [1/3] boto3 インストール中...
pip install boto3

REM AWS CLI 設定
echo.
echo [2/3] AWS認証情報を設定します
"C:\Program Files\Amazon\AWSCLIV2\aws.exe" configure set aws_access_key_id YOUR_AWS_ACCESS_KEY_ID --profile chatwork-webhook
"C:\Program Files\Amazon\AWSCLIV2\aws.exe" configure set aws_secret_access_key YOUR_AWS_SECRET_ACCESS_KEY --profile chatwork-webhook
"C:\Program Files\Amazon\AWSCLIV2\aws.exe" configure set region ap-northeast-1 --profile chatwork-webhook

REM 環境変数設定
setx AWS_PROFILE chatwork-webhook
setx AWS_DEFAULT_REGION ap-northeast-1

echo.
echo [3/3] セットアップ完了
echo.
echo 起動方法: start_poller.bat をダブルクリック
echo.
pause
