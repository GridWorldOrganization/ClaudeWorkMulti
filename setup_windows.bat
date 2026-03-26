@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Chatwork Webhook Poller セットアップ ===
echo.

REM config.env の存在確認
if not exist "config.env" (
    echo [ERROR] config.env が見つかりません
    echo config.env.example をコピーして config.env を作成してください
    goto :END
)

REM config.env から環境変数を読み込む
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("config.env") do (
    if not "%%a"=="" if not "%%b"=="" set "%%a=%%b"
)
echo config.env loaded.
echo.

REM ===== Step 1: Python確認 =====
echo [1/6] Python check...
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python がインストールされていません
    echo.
    echo   以下からインストールしてください:
    echo   https://www.python.org/downloads/
    echo.
    echo   インストール時に「Add Python to PATH」にチェックを入れること
    goto :END
)
python --version
echo   OK
echo.

REM ===== Step 2: pip パッケージインストール =====
echo [2/6] pip install dependencies...
call pip install boto3 requests anthropic google-api-python-client google-auth-httplib2 google-auth-oauthlib
echo   OK
echo.

REM ===== Step 3: Claude Code 確認 =====
echo [3/6] Claude Code check...
call claude --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] claude コマンドが見つかりません
    echo.
    echo   Claude Code がPATHに通っていない可能性があります。
    echo.
    echo   確認方法:
    echo     1. 新しいコマンドプロンプトを開いて call claude --version を実行
    echo     2. 動く場合、このコマンドプロンプトのPATHが古い可能性があります
    echo        一度閉じて開き直してから再実行してください
    echo.
    echo   インストールされていない場合:
    echo     npm install -g @anthropic-ai/claude-code
    echo.
    echo   npmがない場合は Node.js を先にインストール:
    echo     https://nodejs.org/
    goto :END
)
call claude --version
echo   OK
echo.

REM ===== Step 4: AWS CLI 確認 + インストール案内 =====
echo [4/6] AWS CLI check...
call aws --version >nul 2>&1
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
    echo   3. インストール完了後、このコマンドプロンプトを閉じて
    echo      もう一度 setup_windows を実行してください
    goto :END
)
call aws --version
echo   OK
echo.

REM ===== Step 5: AWS プロファイル設定 =====
echo [5/6] AWS profile setup...
if "%AWS_ACCESS_KEY_ID%"=="" if "%AWS_SECRET_ACCESS_KEY%"=="" goto :SKIP_AWS_KEYS
call aws configure set aws_access_key_id %AWS_ACCESS_KEY_ID% --profile chatwork-webhook
call aws configure set aws_secret_access_key %AWS_SECRET_ACCESS_KEY% --profile chatwork-webhook
call aws configure set region ap-northeast-1 --profile chatwork-webhook
echo   OK
goto :AFTER_AWS_KEYS
:SKIP_AWS_KEYS
echo   [SKIP] AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY not set in config.env
echo          OK if AWS profile is already configured.
echo          Otherwise: aws configure --profile chatwork-webhook
:AFTER_AWS_KEYS
echo.

REM ===== Step 6: Google Workspace API 確認 =====
echo [6/6] Google Workspace API check...
python check_gws.py
echo.

echo === Setup Complete ===
echo.
echo Results:
echo   Python:      OK
echo   pip:         OK
echo   Claude Code: OK
echo   AWS CLI:     OK
echo   AWS Profile: OK
echo   Google API:  see [6/6] above
echo.
echo 起動方法: start_poller.bat をダブルクリック

:END
echo.
pause
