"""요약 엔진.

  - 공시: 보고서명 키워드 기반 규칙 분류(항상 동작) + Claude 요약(ANTHROPIC_API_KEY 있을 때)
  - 미디어: Claude 요약, 키가 없으면 키워드 빈도 기반의 간이 요약
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .media import MediaBundle

CLAUDE_MODEL = "claude-opus-5-5"


# ---------------------------------------------------------------- 공시 규칙 분류
@dataclass
class DisclosureRule:
    keywords: tuple[str, ...]
    tone: str  # pos / neg / neu
    importance: int  # 1(낮음) ~ 3(높음)
    reason: str


DISCLOSURE_RULES: list[DisclosureRule] = [
    # --- 부정적 (상장 유지/거래 관련 리스크 우선)
    DisclosureRule(("상장폐지", "상장적격성", "실질심사"), "neg", 3, "상장 유지 관련 리스크 — 거래정지·상장폐지 가능성"),
    DisclosureRule(("매매거래정지", "거래정지"), "neg", 3, "매매거래 정지 — 유동성 차단"),
    DisclosureRule(("관리종목",), "neg", 3, "관리종목 지정 관련 — 투자 위험 확대"),
    DisclosureRule(("횡령", "배임"), "neg", 3, "횡령·배임 혐의 — 지배구조 리스크, 거래정지 사유가 될 수 있음"),
    DisclosureRule(("감사의견", "의견거절"), "neg", 3, "감사의견 관련 — 비적정 의견이면 상장 유지에 치명적"),
    DisclosureRule(("회생절차", "파산", "부도", "영업정지"), "neg", 3, "존속 관련 중대 이벤트"),
    DisclosureRule(("불성실공시",), "neg", 2, "불성실공시법인 지정 — 벌점 누적 시 관리종목 위험"),
    DisclosureRule(("유상증자",), "neg", 3, "유상증자 — 신주 발행에 따른 지분 희석 (자금 용도 확인 필요)"),
    DisclosureRule(("전환사채", "신주인수권부사채", "교환사채"), "neg", 2, "메자닌 발행 — 향후 주식 전환 시 오버행(희석) 우려"),
    DisclosureRule(("전환가액", "행사가액"), "neg", 2, "전환/행사가액 조정(리픽싱) — 잠재 희석 물량 증가"),
    DisclosureRule(("전환청구권행사", "신주인수권행사", "추가상장"), "neg", 2, "전환·행사로 신주 상장 — 단기 매물 부담"),
    DisclosureRule(("감자",), "neg", 3, "감자 결정 — 무상감자는 통상 재무 악화 신호"),
    DisclosureRule(("자기주식처분", "자기주식 처분"), "neg", 2, "자사주 처분 — 시장 매물 출회 가능"),
    DisclosureRule(("소송",), "neg", 2, "소송 제기/판결 — 결과에 따라 재무 영향"),
    DisclosureRule(("계약해지", "해지"), "neg", 2, "계약 해지 — 기대 매출 축소"),
    DisclosureRule(("최대주주변경", "최대주주 변경", "경영권"), "neu", 3, "최대주주/경영권 변동 — 방향성은 인수 주체에 따라 상이"),
    # --- 긍정적
    DisclosureRule(("자기주식취득", "자기주식 취득", "주식소각", "자기주식소각"), "pos", 3, "자사주 매입/소각 — 주주환원 및 수급 개선"),
    DisclosureRule(("무상증자",), "pos", 2, "무상증자 — 유통주식수 증가, 단기 기대감 유입 경향"),
    DisclosureRule(("현금ㆍ현물배당", "현금배당", "배당결정"), "pos", 2, "배당 결정 — 주주환원"),
    DisclosureRule(("단일판매", "공급계약"), "pos", 3, "공급계약 체결 — 계약금액/매출액 대비 비중 확인 필요"),
    DisclosureRule(("특허",), "pos", 1, "특허 취득 — 기술 경쟁력"),
    DisclosureRule(("신규시설투자", "시설투자"), "pos", 2, "신규 시설투자 — 성장 투자 (자금 부담 동반)"),
    DisclosureRule(("기술이전", "라이선스", "기술수출"), "pos", 3, "기술이전/라이선스 계약 — 마일스톤 수익 기대"),
    DisclosureRule(("임상", "품목허가", "허가"), "neu", 3, "임상/허가 관련 — 결과에 따라 주가 변동성 큼"),
    DisclosureRule(("타법인주식", "출자", "양수", "인수"), "neu", 2, "지분/자산 취득 — 시너지 및 자금 부담 동시 고려"),
    DisclosureRule(("합병", "분할"), "neu", 3, "합병/분할 — 기업가치 및 지분구조 변화"),
    # --- 실적/정기
    DisclosureRule(("영업(잠정)실적", "잠정실적", "매출액또는손익구조", "손익구조"), "neu", 3, "실적 공시 — 시장 기대치 대비 수준이 관건"),
    DisclosureRule(("분기보고서", "반기보고서", "사업보고서"), "neu", 2, "정기 보고서 — 재무 실적 확인"),
    DisclosureRule(("기업설명회", "IR"), "neu", 1, "IR 개최 — 정보 제공 이벤트"),
    # --- 지분 변동
    DisclosureRule(("주식등의대량보유", "대량보유"), "neu", 2, "5% 이상 주주 지분 변동 — 매수/매도 주체 확인"),
    DisclosureRule(("임원ㆍ주요주주", "특정증권등소유상황"), "neu", 1, "내부자 지분 변동 — 매수면 긍정, 매도면 부정 신호"),
]


def classify_disclosure(report_nm: str) -> DisclosureRule:
    name = re.sub(r"\s", "", report_nm or "")
    for rule in DISCLOSURE_RULES:
        if any(k.replace(" ", "") in name for k in rule.keywords):
            return rule
    return DisclosureRule((), "neu", 1, "일반 공시")


# ---------------------------------------------------------------- Claude
def _client(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def claude_generate(api_key: str, system: str, user: str, max_tokens: int = 8000) -> str:
    """단발성 요약 호출. 정책 거절 시 서버측 fallback 으로 자동 재시도."""
    client = _client(api_key)
    resp = client.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        return "_AI 요약을 생성하지 못했습니다 (요청 거절)._"
    return "".join(b.text for b in resp.content if b.type == "text").strip()


SYSTEM_ANALYST = (
    "당신은 한국 코스닥 시장을 담당하는 증권사 애널리스트입니다. 주어진 자료만 근거로 사실 위주로 요약하고, "
    "자료에 없는 수치나 사실을 지어내지 마세요. 투자 권유가 아닌 정보 요약임을 전제로, 한국어 마크다운으로 간결하게 작성합니다."
)


def summarize_media_llm(api_key: str, name: str, bundle: MediaBundle) -> str:
    def block(title, items, n):
        lines = "\n".join(f"- {i.as_prompt_line()}" for i in items[:n]) or "- (없음)"
        return f"## {title} ({len(items)}건)\n{lines}"

    user = (
        f"종목: {name}\n아래는 최근 1주일간 수집된 콘텐츠 목록입니다.\n\n"
        f"{block('뉴스', bundle.news, 40)}\n\n{block('유튜브', bundle.youtube, 20)}\n\n{block('블로그', bundle.blog, 30)}\n\n"
        "다음 형식으로 요약하세요:\n"
        "### 핵심 이슈\n(가장 중요한 이슈 3~5개, 각 1~2문장, 관련 날짜 포함)\n"
        "### 주가 영향 요인\n- 🔺 긍정 요인: …\n- 🔻 부정 요인: …\n"
        "### 채널별 분위기\n(뉴스 / 유튜브 / 블로그 각각 한 줄: 관심도와 톤)\n"
        "### 종합 의견\n(2~3문장: 시장 심리와 단기적으로 주목할 포인트)"
    )
    return claude_generate(api_key, SYSTEM_ANALYST, user)


def summarize_disclosures_llm(api_key: str, name: str, disclosures: list[dict], texts: dict[str, str]) -> str:
    parts = []
    for d in disclosures:
        body = texts.get(d["rcept_no"], "")
        parts.append(
            f"### [{d.get('rcept_dt')}] {d.get('report_nm')} (제출인: {d.get('flr_nm')})\n{body[:4000] or '(본문 없음)'}"
        )
    user = (
        f"종목: {name}\n아래는 최근 1주일간 DART 공시와 본문 발췌입니다.\n\n" + "\n\n".join(parts) + "\n\n"
        "각 공시에 대해 핵심 내용(금액, 비율, 상대방, 일정 등 숫자 중심)과 주가 영향(호재/악재/중립, 이유)을 정리하고, "
        "마지막에 '### 종합: 주가에 영향을 줄 만한 포인트' 섹션으로 투자자가 주목할 내용을 3줄 이내로 요약하세요. "
        "단순 정기/형식 공시는 한 줄로만 언급하세요."
    )
    return claude_generate(api_key, SYSTEM_ANALYST, user)


# ---------------------------------------------------------------- 키 없이 쓰는 간이 요약
STOPWORDS = set(
    "주가 주식 종목 코스닥 투자 기자 오늘 대한 관련 이번 지난 상승 하락 기업 전망 분석 증권 뉴스 영상 블로그 "
    "지금 될까 사도 무엇 이유 있다 했다 한다 위해 통해 대비 기준 가능 이후 오전 오후 최근 주요 시장 거래 국내 올해 지난해".split()
)


def keyword_summary(name: str, bundle: MediaBundle, top_n: int = 12) -> list[tuple[str, int]]:
    tokens: Counter[str] = Counter()
    name_key = name.replace(" ", "")
    for it in bundle.news + bundle.youtube + bundle.blog:
        words = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", f"{it.title} {it.snippet}"))
        for w in words:
            w = re.sub(r"(은|는|이|가|을|를|의|에|와|과|도|로|으로|에서|까지)$", "", w)
            if len(w) < 2 or w in STOPWORDS or w == name_key or re.fullmatch(r"\d+[가-힣]?", w):
                continue
            tokens[w] += 1
    return tokens.most_common(top_n)
