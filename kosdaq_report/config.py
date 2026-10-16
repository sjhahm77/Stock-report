"""API 키 로딩: Streamlit secrets → 환경변수(.env) 순서로 찾는다."""
from __future__ import annotations

import os
from pathlib import Path

KEY_NAMES = {
    "KRX_API_KEY": "KRX Open API 인증키 (필수)",
    "DART_API_KEY": "DART Open API 인증키 (필수)",
    "ANTHROPIC_API_KEY": "Claude API 키 (선택: AI 요약)",
    "NAVER_CLIENT_ID": "네이버 검색 API Client ID (선택: 뉴스/블로그)",
    "NAVER_CLIENT_SECRET": "네이버 검색 API Client Secret (선택)",
    "YOUTUBE_API_KEY": "YouTube Data API v3 키 (선택)",
}


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def get_key(name: str) -> str:
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # secrets.toml 이 없으면 예외가 난다
        pass
    return os.environ.get(name, "")
