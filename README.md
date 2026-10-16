# 코스닥 자동 분석 리포트

코스닥 상장사의 **기업명 또는 종목코드**를 입력하면 아래 순서로 리포트를 자동 생성하는 Streamlit 웹앱입니다.

1. **인터랙티브 주가 차트** (KRX Open API)
   캔들 + 이동평균(5/20/60/120) + 볼린저밴드(선택), 거래량, RSI/MACD 보조지표. 1M/3M/6M/1Y 기간 버튼, 줌/팬, 통합 툴팁.
2. **주가 분석**
   - 기간 수익률(1주~1년)과 코스닥 지수 대비 초과수익, 상대성과 차트(기준일=100)
   - 52주 최고/최저 대비 위치, 연환산 변동성, 베타, RSI, MACD, 이평 배열, 거래량 변화
   - 멀티플: PER·PBR·PSR·EPS·BPS·ROE·영업이익률·부채비율·배당수익률 (DART 재무제표, 최근 4분기 TTM 기준)
   - 자동 체크포인트(이평 돌파/이탈, 골든·데드크로스, 과매수·과매도, 거래 급증, 고/저평가 등)
3. **최근 1주일 뉴스 · 유튜브 · 블로그 요약**
   네이버 검색 API(뉴스/블로그), Google 뉴스 RSS, YouTube 검색 → Claude 로 핵심 이슈·호재/악재 요약 (키가 없으면 키워드 빈도 요약)
4. **최근 1주일 DART 공시 요약**
   공시 목록 + 규칙 기반 호재/악재/중요도 분류, Claude 가 공시 원문을 읽고 금액·조건 중심으로 주가 영향 요약

## 실행

### Windows: `run.bat` 더블클릭
Python 3.10 이상만 설치돼 있으면 됩니다 (설치 시 "Add Python to PATH" 체크).
처음 실행 때 가상환경 생성 → 패키지 설치 → `.env` 메모장 열기(API 키 입력 후 저장·닫기)를 자동으로 하고,
이후에는 바로 앱을 띄웁니다. 브라우저에서 http://localhost:8501 이 열리며, 종료는 검은 창을 닫으면 됩니다.

### 직접 실행 (Mac/Linux 포함)

```bash
pip install -r requirements.txt
cp .env.example .env          # 키 입력 (또는 .streamlit/secrets.toml, 또는 앱 사이드바에서 직접 입력)
streamlit run app.py
```

## 필요한 API 키

| 키 | 필수 | 발급처 / 비고 |
|---|---|---|
| `KRX_API_KEY` | ✅ | [KRX Open API](https://openapi.krx.co.kr) — 인증키 발급 후 **API별 이용신청**이 필요합니다: `코스닥 일별매매정보`, `코스닥 종목기본정보`, `KOSDAQ 시리즈 일별시세정보` |
| `DART_API_KEY` | ✅ | [DART Open API](https://opendart.fss.or.kr) |
| `ANTHROPIC_API_KEY` | 선택 | Claude AI 요약 (뉴스·공시). 없으면 규칙/키워드 기반 요약으로 동작 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | 선택 | [네이버 개발자센터](https://developers.naver.com) 검색 API. 블로그 검색에 필요, 뉴스 품질 향상 |
| `YOUTUBE_API_KEY` | 선택 | YouTube Data API v3. 없으면 유튜브 검색 결과 페이지(이번 주 업로드 필터)를 파싱 |

## 동작 방식 참고

- **KRX 시세 수집**: KRX Open API 는 "특정 일자의 전 종목 시세"를 주는 구조라, 기간 시세를 만들려면 영업일마다 호출합니다
  (1년 조회 시 종목·지수 합쳐 약 600회, 8개 스레드 병렬). 지난 일자 응답은 `.cache/krx/` 에 영구 저장되므로 **첫 조회만 30초~1분**이 걸리고,
  이후에는 어떤 종목이든 즉시 표시됩니다. KRX Open API 의 일일 호출 한도를 고려해 캐시 폴더는 지우지 않는 것을 권장합니다.
- **기준일**: KRX Open API 는 당일 시세를 다음 영업일 아침에 제공하므로, 차트의 마지막 날짜는 보통 직전 영업일입니다.
- **멀티플**: 시가총액·상장주식수는 KRX, 손익·자본은 DART `단일회사 전체 재무제표`(연결 우선, 없으면 별도)에서 가져옵니다.
  분기 보고서 기준일 때는 `직전 사업보고서 + 올해 누적 − 전년 동기 누적` 으로 TTM 을 계산하며, PER/EPS 는 지배주주 순이익 기준입니다.
- **AI 요약**: Claude API(`claude-opus-5-5`)를 사용하며, 정책상 거절 시 서버측 fallback 이 자동 적용됩니다. 사이드바에서 끌 수 있습니다.

## 구조

```
app.py                      Streamlit UI (입력 → 차트 → 분석 → 미디어 → 공시)
kosdaq_report/
  krx.py                    KRX Open API 클라이언트 (일자별 캐시, 종목 검색)
  dart.py                   DART 클라이언트 (고유번호, 공시, 원문, 재무제표 TTM, 배당)
  analysis.py               기술적 지표, 수익률/베타, 멀티플, 자동 코멘트
  charts.py                 Plotly 차트 (상승=빨강/하락=파랑)
  media.py                  뉴스/유튜브/블로그 수집
  summarizer.py             공시 규칙 분류 + Claude 요약
tests/                      네트워크 없이 도는 테스트 (가짜 KRX/DART/RSS 응답)
```

```bash
python -m pytest -q
```

> 본 리포트는 공개 데이터를 자동 분석한 참고 자료이며 투자 권유가 아닙니다.
