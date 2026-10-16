@echo off
setlocal
cd /d "%~dp0"
title 코스닥 자동 분석 리포트

echo ============================================
echo   코스닥 자동 분석 리포트
echo ============================================
echo.

rem ---- 1. 파이썬 찾기 (py 런처 우선, 없으면 python) ----
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [오류] 파이썬을 찾을 수 없습니다.
    echo https://www.python.org 에서 Python 3.10 이상을 설치하세요.
    echo 설치 화면에서 "Add Python to PATH" 를 꼭 체크하세요.
    goto :fail
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [오류] Python 3.10 이상이 필요합니다. 현재 버전:
    %PY% --version
    goto :fail
)

rem ---- 2. 가상환경 만들기 (최초 1회) ----
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 가상환경을 만드는 중...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [오류] 가상환경을 만들지 못했습니다.
        goto :fail
    )
)
set "VPY=.venv\Scripts\python.exe"

rem ---- 3. 패키지 설치 (최초 1회, requirements.txt 가 바뀌면 다시) ----
set "NEED_INSTALL=1"
if exist ".venv\requirements.installed" (
    fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1 && set "NEED_INSTALL="
)
if defined NEED_INSTALL (
    echo [2/3] 필요한 패키지를 설치하는 중... 처음에는 몇 분 걸릴 수 있습니다.
    "%VPY%" -m pip install --upgrade pip >nul
    "%VPY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [오류] 패키지 설치에 실패했습니다. 인터넷 연결을 확인하세요.
        goto :fail
    )
    copy /y requirements.txt ".venv\requirements.installed" >nul
)

rem ---- 4. API 키 파일 (.env) 준비 (최초 1회) ----
if not exist ".env" (
    copy /y .env.example .env >nul
    echo.
    echo [3/3] 메모장에서 API 키를 입력하고 저장한 뒤 메모장을 닫으세요.
    echo       KRX_API_KEY, DART_API_KEY 는 필수입니다.
    echo       나중에 앱 화면 왼쪽 사이드바에서 입력해도 됩니다.
    start /wait notepad .env
)

rem ---- 5. 실행 ----
echo.
echo 앱을 시작합니다. 잠시 후 브라우저가 열립니다: http://localhost:8501
echo 종료하려면 이 창을 닫으세요.
echo.
start "" cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8501"
"%VPY%" -m streamlit run app.py --server.port 8501 --server.headless true --browser.gatherUsageStats false
if errorlevel 1 goto :fail
goto :eof

:fail
echo.
echo 위 메시지를 확인하세요. 아무 키나 누르면 창이 닫힙니다.
pause >nul
exit /b 1
