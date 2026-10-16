"""공통 유틸리티: 숫자 파싱, 디스크 캐시, 날짜 헬퍼."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
CACHE_DIR = Path(os.environ.get("KOSDAQ_REPORT_CACHE", Path(__file__).resolve().parent.parent / ".cache"))


def today_kst() -> date:
    return datetime.now(KST).date()


def to_number(value: Any) -> float | None:
    """'1,234', '-', '', None, '(1,234)' 같은 API 문자열을 float 로 변환한다."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if s in ("", "-", "N/A", "nan"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negative else n


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    for a, b in (("&quot;", '"'), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def business_days(start: date, end: date) -> list[date]:
    """주말을 제외한 날짜 목록 (공휴일은 API 응답이 비어 있는 것으로 걸러진다)."""
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class DiskCache:
    """JSON 기반의 아주 단순한 디스크 캐시. 과거 날짜의 시세처럼 변하지 않는 데이터를 저장한다."""

    def __init__(self, namespace: str, root: Path = CACHE_DIR):
        self.dir = Path(root) / namespace
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)
        if len(safe) > 120:
            safe = safe[:80] + "_" + hashlib.sha1(key.encode()).hexdigest()
        return self.dir / f"{safe}.json"

    def get(self, key: str, max_age_hours: float | None = None) -> Any | None:
        p = self._path(key)
        if not p.exists():
            return None
        if max_age_hours is not None:
            age = datetime.now().timestamp() - p.stat().st_mtime
            if age > max_age_hours * 3600:
                return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: Any) -> None:
        p = self._path(key)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
