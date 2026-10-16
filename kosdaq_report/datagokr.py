"""공공데이터포털(data.go.kr) 금융위원회 시세 API 클라이언트 — KRX Open API 대체용.

  - 금융위원회_주식시세정보 : /1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo
  - 금융위원회_지수시세정보 : /1160100/service/GetMarketIndexInfoService/getStockMarketIndex

KRX Open API 와 달리 기간 조회(beginBasDt~endBasDt)가 되므로 종목 하나당 호출 1~2회로 끝난다.
데이터는 보통 기준일 다음 영업일 오후에 갱신된다.
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import unquote

import pandas as pd
import requests

from .krx import StockInfo, short_code
from .utils import DiskCache, to_number, today_kst

BASE_URL = "https://apis.data.go.kr/1160100/service"
EP_STOCK = "GetStockSecuritiesInfoService/getStockPriceInfo"
EP_INDEX = "GetMarketIndexInfoService/getStockMarketIndex"
API_NAMES = {EP_STOCK: "금융위원회_주식시세정보", EP_INDEX: "금융위원회_지수시세정보"}


class DataGoKrError(RuntimeError):
    pass


class DataGoKrAuthError(DataGoKrError):
    """인증키 오류 또는 활용신청 미승인."""


class DataGoKrClient:
    def __init__(self, service_key: str, session: requests.Session | None = None):
        key = (service_key or "").strip()
        if not key:
            raise DataGoKrError("공공데이터포털 인증키가 설정되지 않았습니다.")
        # 포털은 'Encoding'(%2B 등 포함)/'Decoding' 두 가지 키를 준다. requests 가 다시 인코딩하므로 디코딩 키로 통일.
        self.service_key = unquote(key) if "%" in key else key
        self.session = session or requests.Session()
        self.cache = DiskCache("datagokr")

    def _items(self, endpoint: str, **params) -> list[dict]:
        """모든 페이지를 모아 item 목록을 돌려준다."""
        out: list[dict] = []
        page = 1
        while True:
            resp = self.session.get(
                f"{BASE_URL}/{endpoint}",
                params={"serviceKey": self.service_key, "resultType": "json", "numOfRows": 1000, "pageNo": page, **params},
                timeout=30,
            )
            name = API_NAMES.get(endpoint, endpoint)
            try:
                data = resp.json()
            except ValueError:
                # 인증 오류는 HTTP 상태와 무관하게 XML/텍스트로 온다
                text = resp.text[:300]
                if "SERVICE_KEY" in text or resp.status_code in (401, 403) or "Unauthorized" in text:
                    raise DataGoKrAuthError(
                        f"공공데이터포털 인증 실패: '{name}' 활용신청이 승인됐는지, 인증키(Decoding 키)가 맞는지 확인하세요. "
                        "신규 발급 키는 활성화까지 1시간 정도 걸릴 수 있습니다."
                    )
                raise DataGoKrError(f"공공데이터포털 응답 오류({resp.status_code}): {text}")
            body = data.get("response", {})
            header = body.get("header", {})
            if header.get("resultCode") not in ("00", None):
                raise DataGoKrError(f"공공데이터포털 오류 [{header.get('resultCode')}] {header.get('resultMsg', '')}")
            b = body.get("body", {}) or {}
            items = (b.get("items") or {}).get("item") or []
            if isinstance(items, dict):  # 결과가 1건이면 리스트가 아닌 객체로 온다
                items = [items]
            out.extend(items)
            total = int(b.get("totalCount") or 0)
            if len(out) >= total or not items:
                return out
            page += 1

    # ------------------------------------------------------------------ 종목 목록
    def listing(self) -> list[StockInfo]:
        cached = self.cache.get("listing_latest", max_age_hours=24)
        if cached:
            return [StockInfo(**c) for c in cached]
        d = today_kst()
        for _ in range(10):
            rows = self._items(EP_STOCK, basDt=d.strftime("%Y%m%d"), mrktCls="KOSDAQ")
            if rows:
                break
            d -= timedelta(days=1)
        else:
            raise DataGoKrError("최근 10일간 공공데이터포털 코스닥 시세를 받지 못했습니다.")
        out = [
            StockInfo(code=short_code(r.get("srtnCd", "")), name=r.get("itmsNm", ""), std_code=r.get("isinCd", ""),
                      listed_shares=to_number(r.get("lstgStCnt")))
            for r in rows
        ]
        self.cache.set("listing_latest", [o.__dict__ for o in out])
        return out

    # ------------------------------------------------------------------ 시세
    def price_history(self, code: str, start: date, end: date | None = None) -> pd.DataFrame:
        end = end or today_kst()
        rows = self._items(
            EP_STOCK,
            likeSrtnCd=code,
            mrktCls="KOSDAQ",
            beginBasDt=start.strftime("%Y%m%d"),
            endBasDt=(end + timedelta(days=1)).strftime("%Y%m%d"),  # endBasDt 는 '미만' 조건
        )
        records = []
        for r in rows:
            if short_code(r.get("srtnCd", "")) != code:  # likeSrtnCd 는 부분일치
                continue
            close = to_number(r.get("clpr"))
            if not close:
                continue
            records.append(
                {
                    "date": pd.Timestamp(r["basDt"]),
                    "name": r.get("itmsNm", ""),
                    "open": to_number(r.get("mkp")) or close,
                    "high": to_number(r.get("hipr")) or close,
                    "low": to_number(r.get("lopr")) or close,
                    "close": close,
                    "change": to_number(r.get("vs")),
                    "change_pct": to_number(r.get("fltRt")),
                    "volume": to_number(r.get("trqu")) or 0.0,
                    "value": to_number(r.get("trPrc")) or 0.0,
                    "market_cap": to_number(r.get("mrktTotAmt")),
                    "shares": to_number(r.get("lstgStCnt")),
                    "section": "",
                }
            )
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records).drop_duplicates("date").set_index("date").sort_index()
        return df

    def kosdaq_index(self, start: date, end: date | None = None) -> pd.Series:
        end = end or today_kst()
        rows = self._items(
            EP_INDEX,
            idxNm="코스닥",
            beginBasDt=start.strftime("%Y%m%d"),
            endBasDt=(end + timedelta(days=1)).strftime("%Y%m%d"),
        )
        vals = {
            pd.Timestamp(r["basDt"]): to_number(r.get("clpr"))
            for r in rows
            if (r.get("idxNm") or "").strip() == "코스닥" and to_number(r.get("clpr"))
        }
        return pd.Series(vals, name="kosdaq", dtype=float).sort_index()
