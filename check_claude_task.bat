@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   Claude プロセス チェッカー
echo ==========================================
echo.

REM 全claude関連プロセスを取得
echo [実行中の Claude プロセス一覧]
echo.

setlocal enabledelayedexpansion

set COUNT=0
set "PIDS="

for /f "tokens=1,2 delims=," %%A in ('tasklist /FO CSV /NH 2^>nul ^| findstr /i "claude"') do (
    set /a COUNT+=1
    set "PROC_NAME=%%~A"
    set "PROC_PID=%%~B"
    set "PIDS=!PIDS! !PROC_PID!"
    set "PID_!COUNT!=!PROC_PID!"
    set "NAME_!COUNT!=!PROC_NAME!"

    REM 登録PIDかチェック
    set "REGISTERED="
    if exist ".claude_pids" (
        for /f "usebackq" %%P in (".claude_pids") do (
            if "%%P"=="!PROC_PID!" set "REGISTERED= [ポーラー登録]"
        )
    )
    echo   !COUNT!. !PROC_NAME!  PID=!PROC_PID!!REGISTERED!
)

if %COUNT%==0 (
    echo   Claude プロセスは見つかりませんでした。
    echo.
    goto :PIDFILE
)

echo.
echo ------------------------------------------

:PIDFILE
echo.
echo [.claude_pids ファイルの内容]
if exist ".claude_pids" (
    set PIDCOUNT=0
    for /f "usebackq" %%P in (".claude_pids") do (
        set /a PIDCOUNT+=1
        echo   PID=%%P
    )
    if !PIDCOUNT!==0 (
        echo   （空）
    )
) else (
    echo   ファイルなし（正常）
)
echo.
echo ==========================================

if %COUNT%==0 goto :END

echo.
echo 操作を選択してください:
echo   番号  : そのプロセスを終了
echo   a     : 全プロセスを終了
echo   c     : .claude_pids をクリア
echo   q     : 何もせず終了
echo.
set /p CHOICE="選択: "

if /i "%CHOICE%"=="q" goto :END
if /i "%CHOICE%"=="c" (
    if exist ".claude_pids" (
        del ".claude_pids"
        echo .claude_pids をクリアしました。
    ) else (
        echo .claude_pids は存在しません。
    )
    goto :END
)
if /i "%CHOICE%"=="a" (
    echo 全プロセスを終了します...
    for /L %%i in (1,1,%COUNT%) do (
        call set "KILL_PID=%%PID_%%i%%"
        taskkill /F /PID !KILL_PID! >nul 2>&1
        echo   PID=!KILL_PID! を終了しました
    )
    if exist ".claude_pids" del ".claude_pids"
    echo 完了。
    goto :END
)

REM 番号指定
set /a NUM=%CHOICE% 2>nul
if %NUM% GEQ 1 if %NUM% LEQ %COUNT% (
    call set "KILL_PID=%%PID_%NUM%%%"
    taskkill /F /PID !KILL_PID! >nul 2>&1
    echo PID=!KILL_PID! を終了しました。
    goto :END
)

echo 無効な入力です。

:END
echo.
endlocal
pause
