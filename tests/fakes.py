"""KRX / DART / 미디어 API 응답을 흉내 내는 가짜 HTTP 레이어 (네트워크 없이 테스트용)."""
from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

CODE, NAME = "247540", "에코프로비엠"
CORP_CODE = "01160363"


class FakeResponse:
    def __init__(self, payload=None, content: bytes | None = None, status: int = 200, text: str | None = None):
        self._payload = payload
        self.status_code = status
        if content is not None:
            self.content = content
        elif text is not None:
            self.content = text.encode()
        else:
            self.content = json.dumps(payload, ensure_ascii=False).encode()
        self.text = text if text is not None else self.content.decode("utf-8", "ignore")

    def json(self):
        return self._payload if self._payload is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _price(d: date) -> float:
    t = (d - date(2025, 1, 1)).days
    return round(100000 + 15000 * math.sin(t / 40) + 60 * t, -1)


def krx_daily(bas_dd: str) -> list[dict]:
    d = datetime.strptime(bas_dd, "%Y%m%d").date()
    if d.weekday() >= 5 or d.strftime("%m%d") in ("1003", "1009"):  # 주말/공휴일
        return []
    c = _price(d)
    prev = _price(d - timedelta(days=1))
    rows = [
        {
            "BAS_DD": bas_dd, "ISU_CD": CODE, "ISU_NM": NAME, "MKT_NM": "KOSDAQ", "SECT_TP_NM": "우량기업부",
            "TDD_CLSPRC": f"{c:,.0f}", "CMPPREVDD_PRC": f"{c - prev:,.0f}", "FLUC_RT": f"{(c / prev - 1) * 100:.2f}",
            "TDD_OPNPRC": f"{prev:,.0f}", "TDD_HGPRC": f"{max(c, prev) * 1.02:,.0f}", "TDD_LWPRC": f"{min(c, prev) * 0.98:,.0f}",
            "ACC_TRDVOL": f"{500000 + (d.day * 37000) % 900000:,}", "ACC_TRDVAL": f"{c * 600000:,.0f}",
            "MKTCAP": f"{c * 97801344:,.0f}", "LIST_SHRS": "97,801,344",
        },
        {
            "BAS_DD": bas_dd, "ISU_CD": "086520", "ISU_NM": "에코프로", "MKT_NM": "KOSDAQ", "SECT_TP_NM": "우량기업부",
            "TDD_CLSPRC": "80,000", "CMPPREVDD_PRC": "0", "FLUC_RT": "0.00", "TDD_OPNPRC": "80,000",
            "TDD_HGPRC": "81,000", "TDD_LWPRC": "79,000", "ACC_TRDVOL": "1", "ACC_TRDVAL": "1",
            "MKTCAP": "1", "LIST_SHRS": "1",
        },
    ]
    return rows


def krx_index(bas_dd: str) -> list[dict]:
    d = datetime.strptime(bas_dd, "%Y%m%d").date()
    if not krx_daily(bas_dd):
        return []
    t = (d - date(2025, 1, 1)).days
    return [
        {"BAS_DD": bas_dd, "IDX_CLSS": "KOSDAQ", "IDX_NM": "코스닥 150", "CLSPRC_IDX": "1,200.00"},
        {"BAS_DD": bas_dd, "IDX_CLSS": "KOSDAQ", "IDX_NM": "코스닥", "CLSPRC_IDX": f"{800 + 40 * math.sin(t / 60) + 0.1 * t:,.2f}"},
    ]


def krx_base_info(bas_dd: str) -> list[dict]:
    if not krx_daily(bas_dd):
        return []
    return [
        {"ISU_CD": "KR7247540008", "ISU_SRT_CD": CODE, "ISU_NM": "에코프로비엠보통주", "ISU_ABBRV": NAME,
         "LIST_DD": "20190305", "MKT_TP_NM": "KOSDAQ", "SECT_TP_NM": "우량기업부", "KIND_STKCERT_TP_NM": "보통주",
         "PARVAL": "500", "LIST_SHRS": "97801344"},
        {"ISU_CD": "KR7086520004", "ISU_SRT_CD": "086520", "ISU_NM": "에코프로보통주", "ISU_ABBRV": "에코프로",
         "LIST_DD": "20070720", "MKT_TP_NM": "KOSDAQ", "SECT_TP_NM": "우량기업부", "KIND_STKCERT_TP_NM": "보통주",
         "PARVAL": "500", "LIST_SHRS": "133000000"},
        {"ISU_CD": "KR7900000000", "ISU_SRT_CD": "900000", "ISU_NM": "에코프로우", "ISU_ABBRV": "에코프로우",
         "KIND_STKCERT_TP_NM": "우선주", "LIST_SHRS": "1"},
    ]


def corp_code_zip() -> bytes:
    xml = (
        "<?xml version='1.0' encoding='UTF-8'?><result>"
        f"<list><corp_code>{CORP_CODE}</corp_code><corp_name>{NAME}</corp_name><stock_code>{CODE}</stock_code></list>"
        "<list><corp_code>00000001</corp_code><corp_name>비상장사</corp_name><stock_code> </stock_code></list>"
        "</result>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def _fs_row(sj, acc_id, nm, **amounts):
    return {"sj_div": sj, "account_id": acc_id, "account_nm": nm, **{k: f"{v:,}" for k, v in amounts.items()}}


def dart_fs(year: int, reprt_code: str, fs_div: str) -> list[dict]:
    if fs_div != "CFS":
        return []
    if reprt_code == "11012" and year == date.today().year:  # 최신 = 올해 반기
        return [
            _fs_row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업 소유주지분", thstrm_amount=2_000_000_000_000),
            _fs_row("BS", "ifrs-full_Liabilities", "부채총계", thstrm_amount=1_800_000_000_000),
            _fs_row("BS", "ifrs-full_Assets", "자산총계", thstrm_amount=4_000_000_000_000),
            _fs_row("IS", "ifrs-full_Revenue", "매출액", thstrm_amount=700_000_000_000, thstrm_add_amount=1_400_000_000_000,
                    frmtrm_add_amount=1_600_000_000_000),
            _fs_row("IS", "dart_OperatingIncomeLoss", "영업이익", thstrm_amount=20_000_000_000, thstrm_add_amount=50_000_000_000,
                    frmtrm_add_amount=30_000_000_000),
            _fs_row("IS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익",
                    thstrm_amount=15_000_000_000, thstrm_add_amount=40_000_000_000, frmtrm_add_amount=10_000_000_000),
        ]
    if reprt_code == "11011" and year == date.today().year - 1:
        return [
            _fs_row("IS", "ifrs-full_Revenue", "매출액", thstrm_amount=3_000_000_000_000),
            _fs_row("IS", "dart_OperatingIncomeLoss", "영업이익", thstrm_amount=80_000_000_000),
            _fs_row("IS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "당기순이익", thstrm_amount=60_000_000_000),
        ]
    return []


def dart_list() -> dict:
    today = date.today()
    return {
        "status": "000", "message": "정상", "page_no": 1, "total_page": 1,
        "list": [
            {"corp_code": CORP_CODE, "corp_name": NAME, "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": "20261014000111",
             "flr_nm": NAME, "rcept_dt": (today - timedelta(days=2)).strftime("%Y%m%d")},
            {"corp_code": CORP_CODE, "corp_name": NAME, "report_nm": "전환사채권발행결정", "rcept_no": "20261013000222",
             "flr_nm": NAME, "rcept_dt": (today - timedelta(days=3)).strftime("%Y%m%d")},
            {"corp_code": CORP_CODE, "corp_name": NAME, "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서",
             "rcept_no": "20261012000333", "flr_nm": "홍길동", "rcept_dt": (today - timedelta(days=4)).strftime("%Y%m%d")},
        ],
    }


def dart_document() -> bytes:
    html = "<html><body><p>1. 계약 내용: 양극재 공급계약</p><table><tr><td>계약금액</td><td>1,234억원</td></tr></table></body></html>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("20261014000111.xml", html.encode("utf-8"))
    return buf.getvalue()


def rss() -> bytes:
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    return f"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>{NAME}, 북미 양극재 수주 확대 - 한국경제</title><link>https://example.com/1</link><pubDate>{now}</pubDate><source url="https://hankyung.com">한국경제</source></item>
<item><title>{NAME} 전환사채 발행에 주가 약세 - 매일경제</title><link>https://example.com/2</link><pubDate>{now}</pubDate><source url="https://mk.co.kr">매일경제</source></item>
<item><title>2차전지 업황 점검 - 연합뉴스</title><link>https://example.com/3</link><pubDate>{now}</pubDate><source url="https://yna.co.kr">연합뉴스</source></item>
</channel></rss>""".encode()


def youtube_html() -> str:
    data = {"contents": {"sectionListRenderer": {"contents": [{"itemSectionRenderer": {"contents": [
        {"videoRenderer": {"videoId": "abc123", "title": {"runs": [{"text": f"{NAME} 주가 전망, 지금 사도 될까?"}]},
                           "ownerText": {"runs": [{"text": "주식채널"}]}, "publishedTimeText": {"simpleText": "2일 전"},
                           "detailedMetadataSnippets": [{"snippetText": {"runs": [{"text": "양극재 수주와 CB 발행 분석"}]}}]}},
        {"videoRenderer": {"videoId": "zzz", "title": {"runs": [{"text": "오늘의 증시 마감"}]}, "ownerText": {"runs": [{"text": "x"}]}}},
    ]}}]}}}
    return f"<html><script>var ytInitialData = {json.dumps(data, ensure_ascii=False)};</script></html>"


def fake_get(url, params=None, headers=None, timeout=None, **kw):
    params = params or {}
    if "url" in kw:
        url = kw["url"]
    path = urlparse(url).path
    if "data-dbg.krx.co.kr" in url:
        bas = params["basDd"]
        if path.endswith("ksq_bydd_trd"):
            return FakeResponse({"OutBlock_1": krx_daily(bas)})
        if path.endswith("ksq_isu_base_info"):
            return FakeResponse({"OutBlock_1": krx_base_info(bas)})
        if path.endswith("kosdaq_dd_trd"):
            return FakeResponse({"OutBlock_1": krx_index(bas)})
    if "opendart.fss.or.kr" in url:
        if path.endswith("corpCode.xml"):
            return FakeResponse(content=corp_code_zip())
        if path.endswith("fnlttSinglAcntAll.json"):
            rows = dart_fs(int(params["bsns_year"]), params["reprt_code"], params["fs_div"])
            return FakeResponse({"status": "000" if rows else "013", "list": rows})
        if path.endswith("alotMatter.json"):
            return FakeResponse({"status": "000", "list": [
                {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "1,000"}]})
        if path.endswith("list.json"):
            return FakeResponse(dart_list())
        if path.endswith("document.xml"):
            return FakeResponse(content=dart_document())
    if "news.google.com" in url:
        return FakeResponse(content=rss())
    if "youtube.com/results" in url:
        return FakeResponse(text=youtube_html())
    raise AssertionError(f"unexpected URL {url} {params}")


def install(monkeypatch=None):
    """requests.get / Session.get 을 가짜로 교체."""
    import requests

    def session_get(self, url, **kw):
        return fake_get(url, **kw)

    if monkeypatch is not None:
        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr(requests.Session, "get", session_get)
    else:
        requests.get = fake_get
        requests.Session.get = session_get
