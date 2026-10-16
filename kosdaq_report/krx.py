"""KRX Open API(https://openapi.krx.co.kr) 클라이언트.

KRX Open API 는 '특정 일자의 전 종목 시세'를 돌려주는 구조라서, 한 종목의 기간 시세를 만들려면
영업일마다 한 번씩 호출해야 한다. 대신 한 번 받은 일자 데이터는 모든 종목에 재사용할 수 있으므로
과거 일자는 디스크에 캐시해 두고, 두 번째 조회부터는 거의 즉시 응답한다.

사용 API (KRX Open API 에서 각각 '이용 신청'이 필요):
  - 코스닥 일별매매정보   : /svc/apis/sto/ksq_bydd_trd
  - 코스닥 종목기본정보   : /svc/apis/sto/ksq_isu_base_info
  - KOSDAQ 시리즈 일별시세: /svc/apis/idx/kosdaq_dd_trd
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests

from .utils import DiskCache, business_days, to_number, today_kst

BASE_URL = "https://data-dbg.krx.co.kr/svc/apis"
EP_DAILY = "sto/ksq_bydd_trd"
EP_BASE_INFO = "sto/ksq_isu_base_info"
EP_INDEX = "idx/kosdaq_dd_trd"


API_NAMES = {
    EP_DAILY: "코스닥 일별매매정보",
    EP_BASE_INFO: "코스닥 종목기본정보",
    EP_INDEX: "KOSDAQ 시리즈 일별시세정보",
}


class KrxError(RuntimeError):
    pass


class KrxAuthError(KrxError):
    """인증키가 틀렸거나 해당 API 이용신청이 승인되지 않은 경우 (HTTP 401/403)."""


@dataclass
class StockInfo:
    code: str  # 6자리 단축코드
    name: str
    std_code: str = ""
    market: str = "KOSDAQ"
    sector: str = ""  # 소속부 (우량기업부, 벤처기업부 …)
    list_date: str = ""
    par_value: float | None = None
    listed_shares: float | None = None


def short_code(isu_cd: str) -> str:
    """'KR7247540008' 같은 표준코드도 '247540' 단축코드로 정규화."""
    isu_cd = (isu_cd or "").strip()
    if len(isu_cd) == 12 and isu_cd.startswith("KR"):
        return isu_cd[3:9]
    return isu_cd.lstrip("A")


class KrxClient:
    def __init__(self, auth_key: str, session: requests.Session | None = None, max_workers: int = 8):
        if not auth_key:
            raise KrxError("KRX API 키가 설정되지 않았습니다.")
        self.auth_key = auth_key.strip()  # 복사·붙여넣기 시 섞여 들어간 공백/줄바꿈 제거
        self.session = session or requests.Session()
        self.max_workers = max_workers
        self.cache = DiskCache("krx")

    # ------------------------------------------------------------------ 저수준
    def _request(self, endpoint: str, bas_dd: str) -> list[dict]:
        resp = self.session.get(
            f"{BASE_URL}/{endpoint}",
            params={"basDd": bas_dd},
            headers={"AUTH_KEY": self.auth_key},
            timeout=20,
        )
        if resp.status_code in (401, 403):
            raise KrxAuthError(
                f"KRX API 인증 실패({resp.status_code}): '{API_NAMES.get(endpoint, endpoint)}' API 를 호출할 권한이 없습니다. "
                "KRX Open API 사이트에서 인증키가 맞는지, 이 API 의 이용신청이 '승인' 상태인지 확인하세요."
            )
        resp.raise_for_status()
        data = resp.json()
        if "OutBlock_1" not in data:
            msg = data.get("respMsg") or data.get("message") or str(data)[:200]
            raise KrxError(f"KRX API 오류: {msg}")
        return data["OutBlock_1"] or []

    def fetch_day(self, endpoint: str, day: date) -> list[dict]:
        """하루치 전체 데이터. 지난 날짜는 영구 캐시(휴장일의 빈 응답 포함)."""
        bas_dd = day.strftime("%Y%m%d")
        key = f"{endpoint.replace('/', '_')}_{bas_dd}"
        cacheable = day < today_kst()
        if cacheable:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        rows = self._request(endpoint, bas_dd)
        if cacheable:
            self.cache.set(key, rows)
        return rows

    def _fetch_range(self, endpoint: str, start: date, end: date) -> dict[date, list[dict]]:
        days = business_days(start, end)
        if not days:
            return {}
        # 마지막 날을 먼저 호출해, 인증 오류면 수백 건을 병렬로 보내기 전에 바로 실패시킨다
        out = {days[-1]: self.fetch_day(endpoint, days[-1])}
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            out.update(zip(days[:-1], pool.map(lambda d: self.fetch_day(endpoint, d), days[:-1])))
        return dict(sorted(out.items()))

    def _latest_rows(self, endpoint: str) -> list[dict]:
        """오늘부터 거꾸로 최대 10일을 훑어 데이터가 있는 가장 최근 영업일의 응답을 돌려준다."""
        d = today_kst()
        for _ in range(10):
            rows = self.fetch_day(endpoint, d)
            if rows:
                return rows
            d -= timedelta(days=1)
        raise KrxError(f"최근 10일간 '{API_NAMES.get(endpoint, endpoint)}' 데이터를 받지 못했습니다.")

    def listing(self) -> list[StockInfo]:
        """가장 최근 영업일 기준 코스닥 종목 목록 (하루 캐시).

        '종목기본정보' API 를 우선 쓰고, 이용신청이 안 된 경우 '일별매매정보' 응답으로 목록을 만든다.
        """
        cached = self.cache.get("listing_latest", max_age_hours=24)
        if cached:
            return [StockInfo(**c) for c in cached]
        try:
            rows = self._latest_rows(EP_BASE_INFO)
            out = [
                StockInfo(
                    code=r.get("ISU_SRT_CD") or short_code(r.get("ISU_CD", "")),
                    name=r.get("ISU_ABBRV") or r.get("ISU_NM", ""),
                    std_code=r.get("ISU_CD", ""),
                    sector=r.get("SECT_TP_NM", ""),
                    list_date=r.get("LIST_DD", ""),
                    par_value=to_number(r.get("PARVAL")),
                    listed_shares=to_number(r.get("LIST_SHRS")),
                )
                for r in rows
                if (r.get("KIND_STKCERT_TP_NM") or "보통주") == "보통주"
            ]
        except KrxAuthError:
            rows = self._latest_rows(EP_DAILY)
            out = [
                StockInfo(
                    code=short_code(r.get("ISU_CD", "")),
                    name=r.get("ISU_NM", ""),
                    sector=r.get("SECT_TP_NM", ""),
                    listed_shares=to_number(r.get("LIST_SHRS")),
                )
                for r in rows
            ]
        self.cache.set("listing_latest", [o.__dict__ for o in out])
        return out

    # ------------------------------------------------------------------ 시세
    def price_history(self, code: str, start: date, end: date | None = None) -> pd.DataFrame:
        """단일 종목 일별 OHLCV + 시가총액 DataFrame (index: 날짜)."""
        end = end or today_kst()
        by_day = self._fetch_range(EP_DAILY, start, end)
        records = []
        for day, rows in by_day.items():
            for r in rows:
                if short_code(r.get("ISU_CD", "")) != code:
                    continue
                close = to_number(r.get("TDD_CLSPRC"))
                if not close:
                    continue
                records.append(
                    {
                        "date": pd.Timestamp(day),
                        "name": r.get("ISU_NM", ""),
                        "open": to_number(r.get("TDD_OPNPRC")) or close,
                        "high": to_number(r.get("TDD_HGPRC")) or close,
                        "low": to_number(r.get("TDD_LWPRC")) or close,
                        "close": close,
                        "change": to_number(r.get("CMPPREVDD_PRC")),
                        "change_pct": to_number(r.get("FLUC_RT")),
                        "volume": to_number(r.get("ACC_TRDVOL")) or 0.0,
                        "value": to_number(r.get("ACC_TRDVAL")) or 0.0,
                        "market_cap": to_number(r.get("MKTCAP")),
                        "shares": to_number(r.get("LIST_SHRS")),
                        "section": r.get("SECT_TP_NM", ""),
                    }
                )
                break
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records).set_index("date").sort_index()

    def kosdaq_index(self, start: date, end: date | None = None) -> pd.Series:
        end = end or today_kst()
        by_day = self._fetch_range(EP_INDEX, start, end)
        vals = {}
        for day, rows in by_day.items():
            for r in rows:
                if (r.get("IDX_NM") or "").strip() == "코스닥":
                    v = to_number(r.get("CLSPRC_IDX"))
                    if v:
                        vals[pd.Timestamp(day)] = v
                    break
        return pd.Series(vals, name="kosdaq", dtype=float).sort_index()


def search_stocks(query: str, listing: list[StockInfo], limit: int = 20) -> list[StockInfo]:
    """종목코드/종목명으로 검색. 정확히 일치 → 앞부분 일치 → 포함 순서로 정렬."""
    q = query.strip().upper().lstrip("A")
    if not q:
        return []
    if q.isdigit():
        exact = [s for s in listing if s.code == q.zfill(6)]
        if exact:
            return exact
        return [s for s in listing if s.code.startswith(q)][:limit]

    def norm(s: str) -> str:
        return s.replace(" ", "").upper()

    nq = norm(q)
    exact = [s for s in listing if norm(s.name) == nq]
    prefix = [s for s in listing if norm(s.name).startswith(nq) and s not in exact]
    contains = [s for s in listing if nq in norm(s.name) and s not in exact and s not in prefix]
    return (exact + prefix + contains)[:limit]
