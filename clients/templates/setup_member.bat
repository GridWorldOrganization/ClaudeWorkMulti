@echo off
chcp 65001 >nul
setlocal

echo ===================================
echo   メンバーフォルダ セットアップ
echo ===================================
echo.

set /p MEMBER_DIR="メンバーフォルダ名を入力 (例: 01_yokota, 02_fujino): "

if "%MEMBER_DIR%"=="" (
    echo エラー: フォルダ名が入力されていません
    pause
    exit /b 1
)

set TARGET=..\%MEMBER_DIR%

if exist "%TARGET%" (
    echo フォルダ %TARGET% は既に存在します
    echo 01_persona.md が未作成の場合のみテンプレートをコピーします
) else (
    mkdir "%TARGET%"
    echo フォルダ %TARGET% を作成しました
)

if not exist "%TARGET%\01_persona.md" (
    copy "01_persona.md.example" "%TARGET%\01_persona.md"
    echo テンプレートをコピーしました: %TARGET%\01_persona.md
    echo.
    echo ★ 次のステップ: %TARGET%\01_persona.md をテキストエディタで開いて、ペルソナ設定を編集してください
) else (
    echo 01_persona.md は既に存在するためスキップしました
)

echo.
pause
