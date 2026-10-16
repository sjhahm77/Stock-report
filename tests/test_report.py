from datetime import timedelta

import pandas as pd
import pytest

from kosdaq_report import analysis, charts, dart, krx, media, summarizer, utils
from kosdaq_report.utils import today_kst
from tests import fakes


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "CACHE_DIR", tmp_path)
    # DiskCache 의 기본 인자는 import 시점에 고정되므로 각 모듈의 DiskCache 를 tmp 경로로 감싼다
    for mod in (krx, dart):
        monkeypatch.setattr(mod, "DiskCache", lambda ns, _root=tmp_path: utils.DiskCache(ns, _root))
    fakes.install(monkeypatch)


def test_to_number():
    assert utils.to_number("1,234") == 1234
    assert utils.to_number("-") is None
    assert utils.to_number("(1,000)") == -1000
    assert utils.to_number("-3.5") == -3.5


def test_search_stocks():
    listing = krx.KrxClient("k").listing()
    assert [s.code for s in listing] == ["247540", "086520"]  # 우선주 제외
    assert krx.search_stocks("247540", listing)[0].name == "에코프로비엠"
    assert krx.search_stocks("A247540", listing)[0].code == "247540"
    res = krx.search_stocks("에코프로", listing)
    assert res[0].name == "에코프로" and len(res) == 2
    assert krx.short_code("KR7247540008") == "247540"


def test_price_history_and_summary():
    client = krx.KrxClient("k")
    start = today_kst() - timedelta(days=420)
    df = client.price_history("247540", start)
    idx = client.kosdaq_index(start)
    assert len(df) > 250 and df["close"].notna().all()
    assert not idx.empty
    s = analysis.summarize_price(df, idx)
    assert s.high_52w >= s.close >= s.low_52w
    assert s.returns["1개월"] is not None and s.index_returns["1개월"] is not None
    assert s.ma_alignment in ("정배열", "역배열", "혼조")
    assert 0 <= s.rsi <= 100
    assert s.signals
    fig = charts.price_chart(df, "에코프로비엠", "MACD", show_bb=True)
    assert len(fig.data) >= 8
    assert charts.relative_chart(df["close"], idx, "x").data


def test_fundamentals_ttm_and_valuation():
    c = dart.DartClient("k")
    corp = c.corp_code_for("247540")
    assert corp == fakes.CORP_CODE
    f = c.fundamentals(corp)
    assert f.ttm_method.startswith("최근 4개 분기")
    assert f.revenue_ttm == 3_000e9 + 1_400e9 - 1_600e9
    assert f.net_income_ttm == 60e9 + 40e9 - 10e9
    assert f.equity == 2_000e9 and f.dps == 1000
    v = analysis.compute_valuation(100_000, 100_000 * 97_801_344, 97_801_344, f)
    assert v.per == pytest.approx(100_000 * 97_801_344 / 90e9)
    assert v.pbr == pytest.approx(100_000 * 97_801_344 / 2_000e9)
    assert v.roe == pytest.approx(4.5)
    assert v.div_yield == pytest.approx(1.0)


def test_disclosures_and_document():
    c = dart.DartClient("k")
    items = c.disclosures(fakes.CORP_CODE, today_kst() - timedelta(days=7))
    assert len(items) == 3 and items[0]["url"].endswith(items[0]["rcept_no"])
    text = c.document_text(items[0]["rcept_no"])
    assert "계약금액" in text and "1,234억원" in text and "<" not in text


@pytest.mark.parametrize(
    "name,tone,imp",
    [
        ("단일판매ㆍ공급계약체결", "pos", 3),
        ("단일판매ㆍ공급계약해지", "neg", 2),
        ("전환사채권발행결정", "neg", 2),
        ("[기재정정]유상증자결정(제3자배정증자)", "neg", 3),
        ("주요사항보고서(자기주식취득결정)", "pos", 3),
        ("임원ㆍ주요주주특정증권등소유상황보고서", "neu", 1),
        ("기타경영사항(자율공시)", "neu", 1),
    ],
)
def test_classify_disclosure(name, tone, imp):
    r = summarizer.classify_disclosure(name)
    assert (r.tone, r.importance) == (tone, imp)


def test_media_collect_without_keys():
    b = media.collect_media("에코프로비엠", {}, 7)
    assert {i.source for i in b.news} == {"한국경제", "매일경제"}  # 종목명 없는 기사는 제외
    assert all("에코프로비엠" in i.title for i in b.news)
    assert len(b.youtube) == 1 and b.youtube[0].url.endswith("abc123")
    assert b.blog == [] and any("네이버" in e for e in b.errors)
    kw = dict(summarizer.keyword_summary("에코프로비엠", b))
    assert "양극재" in kw


def test_rsi_bounds_on_monotonic_series():
    idx = pd.bdate_range("2026-01-01", periods=60)
    df = pd.DataFrame({"open": range(60), "high": range(60), "low": range(60), "close": [100 + i for i in range(60)],
                       "volume": [1.0] * 60}, index=idx)
    out = analysis.add_indicators(df)
    assert out["rsi"].iloc[-1] == 100


def test_claude_request_shape(monkeypatch):
    import json as _json

    import anthropic
    import httpx2

    seen = {}

    def handler(request):
        seen["body"] = _json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": summarizer.CLAUDE_MODEL,
            "content": [{"type": "text", "text": "### 핵심 이슈\n- 요약"}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    monkeypatch.setattr(summarizer, "_client", lambda key: anthropic.Anthropic(
        api_key=key, http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))))
    b = media.collect_media("에코프로비엠", {}, 7)
    out = summarizer.summarize_media_llm("sk-test", "에코프로비엠", b)
    assert out.startswith("### 핵심 이슈")
    assert seen["body"]["model"] == "claude-opus-5-5"
    assert seen["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert "에코프로비엠, 북미 양극재 수주 확대" in seen["body"]["messages"][0]["content"]


def test_listing_falls_back_when_base_info_not_approved(monkeypatch):
    import requests

    original = requests.Session.get

    def deny_base_info(self, url, **kw):
        if url.endswith("ksq_isu_base_info"):
            return fakes.FakeResponse({"respMsg": "Unauthorized API Call"}, status=401)
        return original(self, url, **kw)

    monkeypatch.setattr(requests.Session, "get", deny_base_info)
    listing = krx.KrxClient("  k \n").listing()
    assert {s.code for s in listing} == {"247540", "086520"}
    assert krx.search_stocks("에코프로비엠", listing)[0].code == "247540"


def test_auth_error_names_the_api(monkeypatch):
    import requests

    monkeypatch.setattr(requests.Session, "get",
                        lambda self, url, **kw: fakes.FakeResponse({"respMsg": "Unauthorized"}, status=401))
    with pytest.raises(krx.KrxAuthError, match="코스닥 일별매매정보"):
        krx.KrxClient("k").listing()
