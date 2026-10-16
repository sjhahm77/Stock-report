"""DART Open API(https://opendart.fss.or.kr) 클라이언트.

  - 고유번호(corpCode.xml)      : 종목코드 → DART 고유번호 매핑
  - 공시검색(list.json)          : 최근 공시 목록
  - 공시서류원본(document.xml)   : 공시 본문 텍스트 (요약용)
  - 단일회사 전체 재무제표(fnlttSinglAcntAll.json): PER/PBR 등 멀티플 계산용
  - 배당에 관한 사항(alotMatter.json)
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from xml.etree import ElementTree

import requests

from .utils import DiskCache, strip_html, to_number, today_kst

BASE_URL = "https://opendart.fss.or.kr/api"

# 보고서 코드: 1분기 11013, 반기 11012, 3분기 11014, 사업보고서 11011
REPORT_CODES = {"11013": ("1분기", 1), "11012": ("반기", 2), "11014": ("3분기", 3), "11011": ("사업보고서", 4)}


class DartError(RuntimeError):
    pass


class DartClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        if not api_key:
            raise DartError("DART API 키가 설정되지 않았습니다.")
        self.api_key = api_key.strip()
        self.session = session or requests.Session()
        self.cache = DiskCache("dart")

    def _get_json(self, path: str, **params) -> dict:
        params["crtfc_key"] = self.api_key
        resp = self.session.get(f"{BASE_URL}/{path}", params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        if status not in ("000", "013"):  # 013 = 조회된 데이터 없음
            raise DartError(f"DART API 오류 [{status}] {data.get('message', '')}")
        return data

    # ------------------------------------------------------------ 고유번호
    def corp_codes(self) -> dict[str, dict]:
        """{종목코드: {corp_code, corp_name}} (상장사만, 1주일 캐시)."""
        cached = self.cache.get("corp_codes", max_age_hours=24 * 7)
        if cached:
            return cached
        resp = self.session.get(f"{BASE_URL}/corpCode.xml", params={"crtfc_key": self.api_key}, timeout=60)
        resp.raise_for_status()
        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
        except zipfile.BadZipFile as e:
            raise DartError(f"corpCode.xml 다운로드 실패: {resp.text[:200]}") from e
        root = ElementTree.fromstring(zf.read(zf.namelist()[0]))
        out = {}
        for item in root.iter("list"):
            stock = (item.findtext("stock_code") or "").strip()
            if stock:
                out[stock] = {
                    "corp_code": (item.findtext("corp_code") or "").strip(),
                    "corp_name": (item.findtext("corp_name") or "").strip(),
                }
        self.cache.set("corp_codes", out)
        return out

    def corp_code_for(self, stock_code: str) -> str | None:
        info = self.corp_codes().get(stock_code)
        return info["corp_code"] if info else None

    # ------------------------------------------------------------ 공시
    def disclosures(self, corp_code: str, start: date, end: date | None = None) -> list[dict]:
        end = end or today_kst()
        items, page = [], 1
        while True:
            data = self._get_json(
                "list.json",
                corp_code=corp_code,
                bgn_de=start.strftime("%Y%m%d"),
                end_de=end.strftime("%Y%m%d"),
                page_no=page,
                page_count=100,
            )
            items.extend(data.get("list", []) or [])
            if page >= int(data.get("total_page", 1) or 1):
                break
            page += 1
        for it in items:
            it["url"] = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no')}"
        return items

    def document_text(self, rcept_no: str, max_chars: int = 6000) -> str:
        """공시 원문(zip 안의 XML/HTML)에서 본문 텍스트만 추출."""
        key = f"doc_{rcept_no}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached[:max_chars]
        resp = self.session.get(
            f"{BASE_URL}/document.xml", params={"crtfc_key": self.api_key, "rcept_no": rcept_no}, timeout=30
        )
        resp.raise_for_status()
        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
        except zipfile.BadZipFile:
            return ""
        texts = []
        for name in sorted(zf.namelist()):
            raw = zf.read(name)
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    texts.append(extract_document_text(raw.decode(enc)))
                    break
                except UnicodeDecodeError:
                    continue
        text = "\n".join(t for t in texts if t)
        self.cache.set(key, text[:30000])
        return text[:max_chars]

    # ------------------------------------------------------------ 재무
    def financial_statements(self, corp_code: str, year: int, reprt_code: str) -> list[dict]:
        key = f"fs_{corp_code}_{year}_{reprt_code}"
        cached = self.cache.get(key, max_age_hours=24 * 7)
        if cached is not None:
            return cached
        rows: list[dict] = []
        for fs_div in ("CFS", "OFS"):  # 연결 우선, 없으면 별도
            data = self._get_json(
                "fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=str(year), reprt_code=reprt_code, fs_div=fs_div
            )
            rows = data.get("list", []) or []
            if rows:
                for r in rows:
                    r["_fs_div"] = fs_div
                break
        self.cache.set(key, rows)
        return rows

    def dividend_per_share(self, corp_code: str, year: int) -> float | None:
        data = self._get_json("alotMatter.json", corp_code=corp_code, bsns_year=str(year), reprt_code="11011")
        for r in data.get("list", []) or []:
            se = r.get("se", "")
            if "주당" in se and "현금배당금" in se and (r.get("stock_knd") in (None, "", "보통주")):
                return to_number(r.get("thstrm"))
        return None

    def fundamentals(self, corp_code: str) -> "Fundamentals":
        """가장 최근 보고서 기준 TTM 손익과 자본 등을 계산."""
        latest = self._latest_report(corp_code)
        if latest is None:
            return Fundamentals(note="DART 재무제표를 찾지 못했습니다.")
        year, reprt_code, rows = latest
        f = Fundamentals(
            report_label=f"{year}년 {REPORT_CODES[reprt_code][0]}",
            fs_div="연결" if rows and rows[0].get("_fs_div") == "CFS" else "별도",
        )
        f.equity = pick_amount(rows, BS_EQUITY, ("BS",), "thstrm_amount")
        f.liabilities = pick_amount(rows, BS_LIABILITIES, ("BS",), "thstrm_amount")
        f.assets = pick_amount(rows, BS_ASSETS, ("BS",), "thstrm_amount")

        if reprt_code == "11011":
            f.revenue_ttm = pick_amount(rows, IS_REVENUE, IS_SJ, "thstrm_amount")
            f.op_income_ttm = pick_amount(rows, IS_OPERATING, IS_SJ, "thstrm_amount")
            f.net_income_ttm = pick_amount(rows, IS_NET_OWNERS, IS_SJ, "thstrm_amount")
            f.ttm_method = "사업보고서(연간)"
            f.dps = self._safe_dps(corp_code, year)
        else:
            prev_annual = self.financial_statements(corp_code, year - 1, "11011")
            quarters = REPORT_CODES[reprt_code][1]
            for attr, accounts in (
                ("revenue_ttm", IS_REVENUE),
                ("op_income_ttm", IS_OPERATING),
                ("net_income_ttm", IS_NET_OWNERS),
            ):
                cum_now = pick_amount(rows, accounts, IS_SJ, "thstrm_add_amount") or pick_amount(
                    rows, accounts, IS_SJ, "thstrm_amount"
                )
                cum_prev = pick_amount(rows, accounts, IS_SJ, "frmtrm_add_amount") or pick_amount(
                    rows, accounts, IS_SJ, "frmtrm_q_amount"
                )
                annual_prev = pick_amount(prev_annual, accounts, IS_SJ, "thstrm_amount") if prev_annual else None
                if cum_now is not None and cum_prev is not None and annual_prev is not None:
                    setattr(f, attr, annual_prev + cum_now - cum_prev)
                    f.ttm_method = "최근 4개 분기 합산(TTM)"
                elif cum_now is not None:
                    setattr(f, attr, cum_now * 4 / quarters)
                    f.ttm_method = f"누적 {quarters}개 분기 연환산"
            f.dps = self._safe_dps(corp_code, year - 1)
        return f

    def _safe_dps(self, corp_code: str, year: int) -> float | None:
        try:
            return self.dividend_per_share(corp_code, year)
        except (DartError, requests.RequestException):
            return None

    def _latest_report(self, corp_code: str):
        y = today_kst().year
        candidates = [
            (y, "11014"), (y, "11012"), (y, "11013"),
            (y - 1, "11011"), (y - 1, "11014"), (y - 1, "11012"), (y - 1, "11013"),
            (y - 2, "11011"),
        ]
        for year, code in candidates:
            rows = self.financial_statements(corp_code, year, code)
            if rows:
                return year, code, rows
        return None


@dataclass
class Fundamentals:
    report_label: str = ""
    fs_div: str = ""
    ttm_method: str = ""
    revenue_ttm: float | None = None
    op_income_ttm: float | None = None
    net_income_ttm: float | None = None
    equity: float | None = None
    liabilities: float | None = None
    assets: float | None = None
    dps: float | None = None
    note: str = ""
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- 계정 매칭
IS_SJ = ("IS", "CIS")
# (account_id 후보, account_nm 후보) — id 가 우선, 없으면 이름으로 매칭
IS_REVENUE = (
    ("ifrs-full_Revenue", "ifrs_Revenue"),
    ("매출액", "수익(매출액)", "영업수익", "매출", "영업수익(매출액)"),
)
IS_OPERATING = (("dart_OperatingIncomeLoss",), ("영업이익", "영업이익(손실)", "영업손익"))
IS_NET_OWNERS = (
    ("ifrs-full_ProfitLossAttributableToOwnersOfParent", "ifrs-full_ProfitLoss", "ifrs_ProfitLoss"),
    ("지배기업의 소유주에게 귀속되는 당기순이익", "지배기업 소유주지분", "당기순이익", "당기순이익(손실)", "당기순손익"),
)
BS_EQUITY = (
    ("ifrs-full_EquityAttributableToOwnersOfParent", "ifrs-full_Equity", "ifrs_Equity"),
    ("지배기업 소유주지분", "지배기업의 소유주에게 귀속되는 자본", "자본총계"),
)
BS_LIABILITIES = (("ifrs-full_Liabilities", "ifrs_Liabilities"), ("부채총계",))
BS_ASSETS = (("ifrs-full_Assets", "ifrs_Assets"), ("자산총계",))


def _norm(s: str) -> str:
    return re.sub(r"[\s()（）]", "", s or "")


def pick_amount(rows: list[dict], accounts: tuple, sj_divs: tuple, amount_field: str) -> float | None:
    ids, names = accounts
    scoped = [r for r in rows if r.get("sj_div") in sj_divs]
    for acc_id in ids:  # 우선순위가 앞선 account_id 부터
        for r in scoped:
            if r.get("account_id") == acc_id:
                v = to_number(r.get(amount_field))
                if v is not None:
                    return v
    norm_names = [_norm(n) for n in names]
    for n in norm_names:
        for r in scoped:
            if _norm(r.get("account_nm", "")) == n:
                v = to_number(r.get(amount_field))
                if v is not None:
                    return v
    return None


def extract_document_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<(br|/p|/tr|/title|/td)[^>]*>", "\n", markup)
    text = strip_html(markup.replace("\n", " ␤ ")).replace("␤", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()
