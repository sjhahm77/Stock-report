"""코스닥 종목 자동 분석 리포트 — Streamlit 앱.

실행:  streamlit run app.py
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from kosdaq_report import analysis, charts, summarizer
from kosdaq_report.config import KEY_NAMES, get_key
from kosdaq_report.dart import DartClient, DartError, Fundamentals
from kosdaq_report.krx import KrxClient, KrxError, StockInfo, search_stocks
from kosdaq_report.media import MediaBundle, MediaItem, collect_media
from kosdaq_report.utils import today_kst

st.set_page_config(page_title="코스닥 자동 분석 리포트", page_icon="📈", layout="wide")

TONE_ICON = {"pos": "🔺", "neg": "🔻", "neu": "➖"}
TONE_LABEL = {"pos": "호재", "neg": "악재", "neu": "중립"}
PERIODS = {"6개월": 270, "1년": 420, "2년": 790}  # 이동평균 계산용 여유 기간 포함 (달력일)


# ====================================================================== 포맷터
def fmt_krw(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "-"
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e12:
        return f"{sign}{v / 1e12:,.2f}조원"
    if v >= 1e8:
        return f"{sign}{v / 1e8:,.0f}억원"
    return f"{sign}{v:,.0f}원"


def fmt(v, spec: str = ",.2f", suffix: str = "") -> str:
    return "-" if v is None or pd.isna(v) else f"{v:{spec}}{suffix}"


# ====================================================================== 캐시된 데이터 로더
@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def load_listing(krx_key: str) -> list[StockInfo]:
    return KrxClient(krx_key).listing()


@st.cache_data(ttl=60 * 30, show_spinner=False)
def load_prices(krx_key: str, code: str, days: int) -> tuple[pd.DataFrame, pd.Series]:
    client = KrxClient(krx_key)
    start = today_kst() - timedelta(days=days)
    df = client.price_history(code, start)
    try:
        idx = client.kosdaq_index(start)
    except KrxError:
        idx = pd.Series(dtype=float)  # 지수 API 미신청 시에도 종목 분석은 계속
    return df, idx


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def load_fundamentals(dart_key: str, code: str) -> tuple[str | None, Fundamentals]:
    client = DartClient(dart_key)
    corp_code = client.corp_code_for(code)
    if not corp_code:
        return None, Fundamentals(note="DART 고유번호를 찾지 못했습니다.")
    return corp_code, client.fundamentals(corp_code)


@st.cache_data(ttl=60 * 30, show_spinner=False)
def load_disclosures(dart_key: str, corp_code: str, days: int) -> list[dict]:
    return DartClient(dart_key).disclosures(corp_code, today_kst() - timedelta(days=days))


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def load_document_text(dart_key: str, rcept_no: str) -> str:
    try:
        return DartClient(dart_key).document_text(rcept_no)
    except Exception:
        return ""


@st.cache_data(ttl=60 * 30, show_spinner=False)
def load_media(name: str, keys: tuple[tuple[str, str], ...], days: int) -> MediaBundle:
    return collect_media(name, dict(keys), days)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def llm_media_summary(api_key: str, name: str, bundle: MediaBundle) -> str:
    return summarizer.summarize_media_llm(api_key, name, bundle)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def llm_disclosure_summary(api_key: str, name: str, items: list[dict], texts: dict[str, str]) -> str:
    return summarizer.summarize_disclosures_llm(api_key, name, items, texts)


# ====================================================================== 사이드바
with st.sidebar:
    st.header("⚙️ 설정")
    with st.expander("API 키", expanded=not (get_key("KRX_API_KEY") and get_key("DART_API_KEY"))):
        keys = {
            k: st.text_input(desc, value=get_key(k), type="password", key=f"key_{k}")
            for k, desc in KEY_NAMES.items()
        }
        st.caption("`.streamlit/secrets.toml` 또는 `.env` 에 저장해 두면 자동으로 채워집니다.")
    period_label = st.radio("차트 조회 기간", list(PERIODS), index=1, horizontal=True)
    indicator = st.radio("보조지표", ["RSI", "MACD"], horizontal=True)
    show_bb = st.toggle("볼린저 밴드 표시", value=False)
    media_days = st.slider("뉴스·공시 조회 기간(일)", 3, 30, 7)
    use_llm = st.toggle("Claude AI 요약 사용", value=bool(keys["ANTHROPIC_API_KEY"]),
                        disabled=not keys["ANTHROPIC_API_KEY"],
                        help="ANTHROPIC_API_KEY 가 있으면 뉴스·공시를 Claude 로 요약합니다.")

# ====================================================================== 상단: 종목 입력
st.title("📈 코스닥 자동 분석 리포트")
with st.form("search", border=False):
    c1, c2 = st.columns([5, 1])
    query = c1.text_input("기업명 또는 종목코드", placeholder="예) 에코프로비엠, 247540",
                          label_visibility="collapsed", value=st.session_state.get("query", ""))
    submitted = c2.form_submit_button("분석", type="primary", width="stretch")
if submitted:
    st.session_state["query"] = query.strip()
    st.session_state.pop("picked", None)

query = st.session_state.get("query", "")
if not query:
    st.info("상단에 코스닥 기업명 또는 6자리 종목코드를 입력하고 **분석**을 누르세요.")
    st.stop()

missing = [k for k in ("KRX_API_KEY", "DART_API_KEY") if not keys[k]]
if missing:
    st.error(f"필수 API 키가 없습니다: {', '.join(missing)} — 사이드바에서 입력하세요.")
    st.stop()

try:
    with st.spinner("코스닥 종목 목록 불러오는 중…"):
        listing = load_listing(keys["KRX_API_KEY"])
except Exception as e:
    st.error(f"KRX 종목 목록 조회 실패: {e}")
    st.stop()

matches = search_stocks(query, listing)
if not matches:
    st.warning(f"'{query}' 에 해당하는 코스닥 종목을 찾지 못했습니다. (코스피 종목은 지원하지 않습니다)")
    st.stop()
if len(matches) > 1 and matches[0].name.replace(" ", "") != query.replace(" ", "") and matches[0].code != query.zfill(6):
    labels = [f"{m.name} ({m.code})" for m in matches]
    choice = st.selectbox("여러 종목이 검색되었습니다. 분석할 종목을 선택하세요.", labels, key="picked")
    stock = matches[labels.index(choice)]
else:
    stock = matches[0]

# ====================================================================== 1. 주가 차트
try:
    with st.spinner(f"{stock.name} 시세 불러오는 중… (첫 조회는 일자별 호출로 30초~1분 소요, 이후 캐시)"):
        prices, kosdaq = load_prices(keys["KRX_API_KEY"], stock.code, PERIODS[period_label])
except Exception as e:
    st.error(f"KRX 시세 조회 실패: {e}")
    st.stop()
if prices.empty:
    st.error("시세 데이터가 없습니다. 거래정지 종목이거나 신규 상장 종목일 수 있습니다.")
    st.stop()

summary = analysis.summarize_price(prices, kosdaq if not kosdaq.empty else None)

st.subheader(f"{stock.name}  ·  {stock.code}")
st.caption(f"코스닥 {stock.sector or ''} · 기준일 {summary.last_date:%Y-%m-%d} (KRX 종가 기준)")
m = st.columns(5)
m[0].metric("현재가", f"{summary.close:,.0f}원",
            f"{fmt(summary.change, ',.0f')}원 ({fmt(summary.change_pct, '+.2f')}%)" if summary.change is not None else None,
            delta_color="red" if (summary.change or 0) > 0 else ("blue" if (summary.change or 0) < 0 else "gray"))  # 국내 관례
m[1].metric("시가총액", fmt_krw(summary.market_cap))
m[2].metric("52주 최고", f"{summary.high_52w:,.0f}원", f"{summary.from_high_pct:+.1f}%", delta_color="off")
m[3].metric("52주 최저", f"{summary.low_52w:,.0f}원", f"{summary.from_low_pct:+.1f}%", delta_color="off")
m[4].metric("20일 평균 거래대금", fmt_krw(summary.avg_value_20d))

st.plotly_chart(charts.price_chart(prices, stock.name, indicator, show_bb), width="stretch",
                config={"scrollZoom": True, "displaylogo": False})

# ====================================================================== 2. 주가 분석
st.header("📊 주가 분석")

fund_err = None
corp_code = None
fundamentals = Fundamentals()
try:
    with st.spinner("DART 재무제표 불러오는 중…"):
        corp_code, fundamentals = load_fundamentals(keys["DART_API_KEY"], stock.code)
except Exception as e:
    fund_err = str(e)
valuation = analysis.compute_valuation(summary.close, summary.market_cap, summary.shares, fundamentals)

left, right = st.columns([1.1, 1])
with left:
    st.markdown("##### 기간 수익률")
    rows = []
    for label in analysis.RETURN_PERIODS:
        r, i = summary.returns.get(label), summary.index_returns.get(label)
        rows.append({"기간": label, stock.name: r, "코스닥": i,
                     "초과수익(%p)": (r - i) if r is not None and i is not None else None})
    ret_df = pd.DataFrame(rows).set_index("기간")
    st.dataframe(
        ret_df.style.format("{:+.2f}", na_rep="-").map(
            lambda v: "color:#e0383e" if isinstance(v, float) and v > 0 else ("color:#2a78d6" if isinstance(v, float) and v < 0 else "")
        ),
        width="stretch",
    )
    if kosdaq.empty:
        st.caption("코스닥 지수 데이터를 받지 못해 지수 비교는 생략했습니다. "
                   "KRX Open API 에서 'KOSDAQ 시리즈 일별시세정보' 이용신청 승인 여부를 확인하세요.")
    else:
        st.markdown("##### 코스닥 지수 대비 상대 성과")
        st.plotly_chart(charts.relative_chart(prices["close"].tail(250), kosdaq, stock.name), width="stretch",
                        config={"displaylogo": False})

with right:
    st.markdown("##### 기술적 지표")
    t = st.columns(3)
    t[0].metric("RSI(14)", fmt(summary.rsi, ".1f"))
    t[1].metric("연환산 변동성", fmt(summary.volatility_ann, ".1f", "%"))
    t[2].metric("베타", fmt(summary.beta, ".2f"), help="코스닥 지수 대비, 최근 1년 일간 수익률 기준")
    st.markdown(
        f"이동평균 배열 **{summary.ma_alignment}** · MACD **{summary.macd_state}** · "
        f"거래량(5일/20일 평균) **{fmt(summary.volume_ratio, '.2f', '배')}**"
    )

    st.markdown("##### 밸류에이션")
    v = st.columns(3)
    v[0].metric("PER", fmt(valuation.per, ".1f", "배"))
    v[1].metric("PBR", fmt(valuation.pbr, ".2f", "배"))
    v[2].metric("PSR", fmt(valuation.psr, ".2f", "배"))
    v = st.columns(3)
    v[0].metric("EPS", fmt(valuation.eps, ",.0f", "원"))
    v[1].metric("BPS", fmt(valuation.bps, ",.0f", "원"))
    v[2].metric("ROE", fmt(valuation.roe, ".1f", "%"))
    v = st.columns(3)
    v[0].metric("영업이익률", fmt(valuation.op_margin, ".1f", "%"))
    v[1].metric("부채비율", fmt(valuation.debt_ratio, ".0f", "%"))
    v[2].metric("배당수익률", fmt(valuation.div_yield, ".2f", "%"))
    if fund_err:
        st.warning(f"DART 재무 조회 실패: {fund_err}")
    elif fundamentals.note:
        st.caption(fundamentals.note)
    else:
        st.caption(
            f"재무 기준: {fundamentals.report_label} {fundamentals.fs_div} · 손익은 {fundamentals.ttm_method} · "
            f"매출 {fmt_krw(fundamentals.revenue_ttm)} / 영업이익 {fmt_krw(fundamentals.op_income_ttm)} / "
            f"순이익(지배) {fmt_krw(fundamentals.net_income_ttm)} · 자본(지배) {fmt_krw(fundamentals.equity)}"
        )

st.markdown("##### 체크포인트")
for tone, text in summary.signals + valuation.comments:
    st.markdown(f"{TONE_ICON[tone]} {text}")

# ====================================================================== 3. 뉴스 / 유튜브 / 블로그
st.header(f"📰 최근 {media_days}일 뉴스 · 유튜브 · 블로그")
media_keys = tuple((k, keys[k]) for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "YOUTUBE_API_KEY"))
with st.spinner("뉴스·유튜브·블로그 검색 중…"):
    bundle = load_media(stock.name, media_keys, media_days)

c = st.columns(3)
c[0].metric("뉴스", f"{len(bundle.news)}건")
c[1].metric("유튜브", f"{len(bundle.youtube)}건")
c[2].metric("블로그", f"{len(bundle.blog)}건")

total_items = len(bundle.news) + len(bundle.youtube) + len(bundle.blog)
if total_items:
    if use_llm:
        try:
            with st.spinner("Claude 로 요약 중…"):
                st.markdown(llm_media_summary(keys["ANTHROPIC_API_KEY"], stock.name, bundle))
        except Exception as e:
            st.warning(f"AI 요약 실패: {e}")
    else:
        kw = summarizer.keyword_summary(stock.name, bundle)
        if kw:
            st.markdown("**자주 언급된 키워드:** " + " · ".join(f"`{w}`({n})" for w, n in kw))
        st.caption("ANTHROPIC_API_KEY 를 설정하면 핵심 이슈·호재/악재를 AI 가 요약합니다.")
else:
    st.info("최근 기간 동안 검색된 콘텐츠가 없습니다.")


def render_items(items: list[MediaItem], limit: int = 30) -> None:
    if not items:
        st.caption("검색 결과 없음")
        return
    for it in items[:limit]:
        when = it.published.strftime("%m/%d %H:%M") if it.published else ""
        meta = " · ".join(x for x in (it.source, when) if x)
        st.markdown(f"- [{it.title}]({it.url})  \n  <small>{meta}</small>", unsafe_allow_html=True)
        if it.snippet:
            st.caption(it.snippet[:180])


tabs = st.tabs([f"뉴스 ({len(bundle.news)})", f"유튜브 ({len(bundle.youtube)})", f"블로그 ({len(bundle.blog)})"])
with tabs[0]:
    render_items(bundle.news)
with tabs[1]:
    render_items(bundle.youtube)
with tabs[2]:
    render_items(bundle.blog)
if bundle.errors:
    with st.expander("수집 중 참고사항"):
        for e in bundle.errors:
            st.caption(e)

# ====================================================================== 4. DART 공시
st.header(f"📑 최근 {media_days}일 DART 공시")
if not corp_code:
    st.warning("DART 고유번호를 찾지 못해 공시를 조회할 수 없습니다.")
else:
    try:
        with st.spinner("DART 공시 조회 중…"):
            items = load_disclosures(keys["DART_API_KEY"], corp_code, media_days)
    except Exception as e:
        st.error(f"DART 공시 조회 실패: {e}")
        items = []

    if not items:
        st.info("해당 기간에 제출된 공시가 없습니다.")
    else:
        classified = [(d, summarizer.classify_disclosure(d.get("report_nm", ""))) for d in items]
        table = pd.DataFrame(
            [
                {
                    "접수일": pd.to_datetime(d.get("rcept_dt"), format="%Y%m%d").date(),
                    "영향": f"{TONE_ICON[r.tone]} {TONE_LABEL[r.tone]}",
                    "중요도": "★" * r.importance,
                    "공시명": d.get("report_nm", "").strip(),
                    "제출인": d.get("flr_nm", ""),
                    "해석": r.reason,
                    "원문": d["url"],
                }
                for d, r in classified
            ]
        )
        st.dataframe(table, hide_index=True, width="stretch",
                     column_config={
                         "접수일": st.column_config.DateColumn(width="small"),
                         "영향": st.column_config.TextColumn(width="small"),
                         "중요도": st.column_config.TextColumn(width="small"),
                         "공시명": st.column_config.TextColumn(width="medium"),
                         "제출인": st.column_config.TextColumn(width="small"),
                         "해석": st.column_config.TextColumn(width="large"),
                         "원문": st.column_config.LinkColumn("원문", display_text="열기", width="small"),
                     })

        important = [d for d, r in sorted(classified, key=lambda x: -x[1].importance) if r.importance >= 2][:10]
        st.markdown("##### 주가 영향 요약")
        if use_llm and important:
            texts = {}
            with st.spinner("공시 원문 읽는 중…"):
                for d in important:
                    texts[d["rcept_no"]] = load_document_text(keys["DART_API_KEY"], d["rcept_no"])
            try:
                with st.spinner("Claude 로 공시 요약 중…"):
                    st.markdown(llm_disclosure_summary(keys["ANTHROPIC_API_KEY"], stock.name, important, texts))
            except Exception as e:
                st.warning(f"AI 요약 실패: {e}")
        else:
            notable = [(d, r) for d, r in classified if r.importance >= 2]
            counts = {t: sum(1 for _, r in notable if r.tone == t) for t in TONE_ICON}
            if not notable:
                st.markdown("➖ 주가에 직접적인 영향을 줄 만한 공시는 없습니다 (일반·형식 공시만 제출).")
            else:
                st.markdown(
                    f"주요 공시 {len(notable)}건 — 🔺 호재성 {counts['pos']}건 · 🔻 악재성 {counts['neg']}건 · ➖ 중립 {counts['neu']}건"
                )
                for tone in ("neg", "pos", "neu"):  # 리스크 공시를 먼저 노출
                    names = [d.get("report_nm", "").strip() for d, r in notable if r.tone == tone]
                    if names:
                        st.markdown(f"{TONE_ICON[tone]} **{TONE_LABEL[tone]}**: " + ", ".join(names))
            if not use_llm:
                st.caption("규칙 기반 분류입니다. ANTHROPIC_API_KEY 를 설정하면 공시 원문을 읽고 금액·조건까지 요약합니다.")

st.divider()
st.caption("본 리포트는 공개 데이터(KRX, DART, 뉴스/유튜브/블로그 검색)를 자동 분석한 참고 자료이며 투자 권유가 아닙니다.")
