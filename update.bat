@echo off
setlocal
cd /d "%~dp0"
title 코스닥 자동 분석 리포트 - 업데이트

rem 최신 코드를 받아올 브랜치 (PR 이 main 에 머지되면 main 으로 바꾸면 됩니다)
set "BRANCH=claude/kosdaq-auto-analysis-report-dp5k5i"
set "ZIPURL=https://github.com/sjhahm77/Stock-report/archive/refs/heads/%BRANCH%.zip"
set "TMPDIR=%TEMP%\kosdaq_report_update"

echo ============================================
echo   최신 버전으로 업데이트합니다
echo   (.env 의 API 키와 설치된 패키지는 그대로 유지)
echo ============================================
echo.

if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
mkdir "%TMPDIR%"

echo [1/3] GitHub 에서 최신 코드를 내려받는 중...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '%ZIPURL%' -OutFile '%TMPDIR%\src.zip'"
if errorlevel 1 (
    echo [오류] 내려받기에 실패했습니다. 인터넷 연결을 확인하세요.
    goto :fail
)

echo [2/3] 압축을 푸는 중...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force -Path '%TMPDIR%\src.zip' -DestinationPath '%TMPDIR%\src'"
if errorlevel 1 (
    echo [오류] 압축 해제에 실패했습니다.
    goto :fail
)
set "SRC="
for /d %%D in ("%TMPDIR%\src\*") do set "SRC=%%D"
if not defined SRC (
    echo [오류] 내려받은 파일에서 코드 폴더를 찾지 못했습니다.
    goto :fail
)

echo [3/3] 파일을 교체하는 중...
rem .env(API 키), .venv(설치된 패키지), .cache(시세 캐시), 실행 중인 update.bat 은 건드리지 않음
robocopy "%SRC%" "%~dp0." /E /NFL /NDL /NJH /NJS /NP /XD .venv .cache /XF .env update.bat >nul
if errorlevel 8 (
    echo [오류] 파일 복사에 실패했습니다. 앱^(run.bat 창^)이 켜져 있다면 닫고 다시 실행하세요.
    goto :fail
)

rmdir /s /q "%TMPDIR%"
echo.
echo 업데이트 완료! run.bat 을 실행하세요.
echo 아무 키나 누르면 창이 닫힙니다.
pause >nul
exit /b 0

:fail
echo.
echo 아무 키나 누르면 창이 닫힙니다.
pause >nul
exit /b 1
