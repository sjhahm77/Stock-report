"""주가 기술적 분석 / 밸류에이션 멀티플 계산."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .dart import Fundamentals

MA_WINDOWS = (5, 20, 60, 120)
RETURN_PERIODS = {"1주": 5, "1개월": 21, "3개월": 63, "6개월": 126, "1년": 250}


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]
    for w in MA_WINDOWS:
        df[f"ma{w}"] = close.rolling(w).mean()
    # 볼린저 밴드 (20, 2σ)
    std20 = close.rolling(20).std()
    df["bb_upper"] = df["ma20"] + 2 * std20
    df["bb_lower"] = df["ma20"] - 2 * std20
    # RSI(14) — Wilder 방식
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)
    df.loc[(loss == 0) & gain.notna(), "rsi"] = 100.0
    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    return df


def period_returns(close: pd.Series) -> dict[str, float | None]:
    out = {}
    for label, n in RETURN_PERIODS.items():
        out[label] = (close.iloc[-1] / close.iloc[-1 - n] - 1) * 100 if len(close) > n else None
    return out


@dataclass
class PriceSummary:
    last_date: pd.Timestamp
    close: float
    change: float | None
    change_pct: float | None
    market_cap: float | None
    shares: float | None
    high_52w: float
    low_52w: float
    from_high_pct: float
    from_low_pct: float
    volatility_ann: float | None
    avg_value_20d: float | None
    volume_ratio: float | None  # 최근 5일 평균 거래량 / 20일 평균
    returns: dict[str, float | None]
    index_returns: dict[str, float | None]
    beta: float | None
    rsi: float | None
    macd_state: str
    ma_alignment: str
    signals: list[tuple[str, str]] = field(default_factory=list)  # (tone, text) tone: pos/neg/neu


def summarize_price(df: pd.DataFrame, index: pd.Series | None = None) -> PriceSummary:
    df = add_indicators(df)
    last = df.iloc[-1]
    close = df["close"]
    window = df.tail(250)
    high_52w, low_52w = float(window["high"].max()), float(window["low"].min())
    rets = close.pct_change().dropna()
    vol_ann = float(rets.tail(250).std() * np.sqrt(250) * 100) if len(rets) > 20 else None

    idx_returns: dict[str, float | None] = {k: None for k in RETURN_PERIODS}
    beta = None
    if index is not None and not index.empty:
        aligned = index.reindex(df.index).ffill()
        idx_returns = period_returns(aligned.dropna())
        joined = pd.concat([rets, aligned.pct_change()], axis=1, keys=["s", "m"], sort=True).dropna().tail(250)
        if len(joined) > 30 and joined["m"].var() > 0:
            beta = float(joined.cov().loc["s", "m"] / joined["m"].var())

    vol5, vol20 = df["volume"].tail(5).mean(), df["volume"].tail(20).mean()
    s = PriceSummary(
        last_date=df.index[-1],
        close=float(last["close"]),
        change=last.get("change"),
        change_pct=last.get("change_pct"),
        market_cap=last.get("market_cap"),
        shares=last.get("shares"),
        high_52w=high_52w,
        low_52w=low_52w,
        from_high_pct=(last["close"] / high_52w - 1) * 100,
        from_low_pct=(last["close"] / low_52w - 1) * 100,
        volatility_ann=vol_ann,
        avg_value_20d=float(df["value"].tail(20).mean()) if "value" in df else None,
        volume_ratio=float(vol5 / vol20) if vol20 else None,
        returns=period_returns(close),
        index_returns=idx_returns,
        beta=beta,
        rsi=_f(last.get("rsi")),
        macd_state=_macd_state(df),
        ma_alignment=_ma_alignment(last),
    )
    s.signals = _signals(df, s)
    return s


def _f(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def _ma_alignment(last: pd.Series) -> str:
    mas = [last.get(f"ma{w}") for w in (5, 20, 60, 120)]
    if any(m is None or pd.isna(m) for m in mas):
        return "데이터 부족"
    if mas[0] > mas[1] > mas[2] > mas[3]:
        return "정배열"
    if mas[0] < mas[1] < mas[2] < mas[3]:
        return "역배열"
    return "혼조"


def _macd_state(df: pd.DataFrame) -> str:
    if len(df) < 35:
        return "데이터 부족"
    h = df["macd_hist"]
    if h.iloc[-2] <= 0 < h.iloc[-1]:
        return "골든크로스"
    if h.iloc[-2] >= 0 > h.iloc[-1]:
        return "데드크로스"
    return "상승 우위" if h.iloc[-1] > 0 else "하락 우위"


def _crossed(fast: pd.Series, slow: pd.Series, lookback: int = 5) -> str | None:
    diff = (fast - slow).dropna().tail(lookback + 1)
    if len(diff) < 2:
        return None
    sign = np.sign(diff)
    changes = sign.diff().fillna(0)
    if (changes > 0).any():
        return "golden"
    if (changes < 0).any():
        return "dead"
    return None


def _signals(df: pd.DataFrame, s: PriceSummary) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    last = df.iloc[-1]
    c = s.close

    for w in (20, 60, 120):
        ma = last.get(f"ma{w}")
        if ma is not None and not pd.isna(ma):
            gap = (c / ma - 1) * 100
            tone = "pos" if gap >= 0 else "neg"
            out.append((tone, f"{w}일 이동평균선 대비 {gap:+.1f}% ({'상회' if gap >= 0 else '하회'})"))

    cross = _crossed(df["ma5"], df["ma20"])
    if cross == "golden":
        out.append(("pos", "최근 5거래일 내 5일선이 20일선을 상향 돌파 (단기 골든크로스)"))
    elif cross == "dead":
        out.append(("neg", "최근 5거래일 내 5일선이 20일선을 하향 이탈 (단기 데드크로스)"))
    cross = _crossed(df["ma20"], df["ma60"], lookback=10)
    if cross == "golden":
        out.append(("pos", "최근 10거래일 내 20일선이 60일선을 상향 돌파 (중기 골든크로스)"))
    elif cross == "dead":
        out.append(("neg", "최근 10거래일 내 20일선이 60일선을 하향 이탈 (중기 데드크로스)"))

    if s.rsi is not None:
        if s.rsi >= 70:
            out.append(("neg", f"RSI {s.rsi:.0f} — 과매수 구간, 단기 조정 가능성"))
        elif s.rsi <= 30:
            out.append(("pos", f"RSI {s.rsi:.0f} — 과매도 구간, 기술적 반등 가능성"))
        else:
            out.append(("neu", f"RSI {s.rsi:.0f} — 중립 구간"))

    bb_u, bb_l = last.get("bb_upper"), last.get("bb_lower")
    if bb_u is not None and not pd.isna(bb_u):
        if c > bb_u:
            out.append(("neg", "주가가 볼린저밴드 상단을 돌파 — 과열 신호"))
        elif c < bb_l:
            out.append(("pos", "주가가 볼린저밴드 하단을 이탈 — 과매도 신호"))

    if s.volume_ratio is not None:
        if s.volume_ratio >= 2:
            out.append(("neu", f"최근 5일 거래량이 20일 평균의 {s.volume_ratio:.1f}배 — 거래 급증, 수급 변화 주목"))
        elif s.volume_ratio <= 0.5:
            out.append(("neu", f"최근 5일 거래량이 20일 평균의 {s.volume_ratio:.1f}배 — 거래 위축"))

    if s.from_high_pct > -3:
        out.append(("pos", "52주 신고가 부근에서 거래 중"))
    elif s.from_low_pct < 3:
        out.append(("neg", "52주 신저가 부근에서 거래 중"))

    r1m, i1m = s.returns.get("1개월"), s.index_returns.get("1개월")
    if r1m is not None and i1m is not None:
        diff = r1m - i1m
        out.append(
            ("pos" if diff >= 0 else "neg", f"최근 1개월 코스닥 지수 대비 {diff:+.1f}%p {'아웃퍼폼' if diff >= 0 else '언더퍼폼'}")
        )
    return out


# ---------------------------------------------------------------- 멀티플
@dataclass
class Valuation:
    per: float | None = None
    pbr: float | None = None
    psr: float | None = None
    eps: float | None = None
    bps: float | None = None
    roe: float | None = None
    op_margin: float | None = None
    debt_ratio: float | None = None
    div_yield: float | None = None
    comments: list[tuple[str, str]] = field(default_factory=list)


def compute_valuation(price: float, market_cap: float | None, shares: float | None, f: Fundamentals) -> Valuation:
    v = Valuation()
    if not market_cap and shares:
        market_cap = price * shares
    if not shares and market_cap:
        shares = market_cap / price

    def div(a, b):
        return a / b if a is not None and b not in (None, 0) else None

    if f.net_income_ttm is not None and market_cap:
        v.per = div(market_cap, f.net_income_ttm) if f.net_income_ttm > 0 else None
        v.eps = div(f.net_income_ttm, shares)
    if f.equity and market_cap:
        v.pbr = div(market_cap, f.equity) if f.equity > 0 else None
        v.bps = div(f.equity, shares)
    if f.revenue_ttm and market_cap:
        v.psr = div(market_cap, f.revenue_ttm)
    if f.net_income_ttm is not None and f.equity and f.equity > 0:
        v.roe = f.net_income_ttm / f.equity * 100
    if f.op_income_ttm is not None and f.revenue_ttm:
        v.op_margin = f.op_income_ttm / f.revenue_ttm * 100
    if f.liabilities is not None and f.equity and f.equity > 0:
        v.debt_ratio = f.liabilities / f.equity * 100
    if f.dps:
        v.div_yield = f.dps / price * 100

    c = v.comments
    if f.net_income_ttm is not None and f.net_income_ttm <= 0:
        c.append(("neg", "최근 12개월 순이익이 적자라 PER 산출 불가 (적자 기업)"))
    elif v.per is not None:
        if v.per < 10:
            c.append(("pos", f"PER {v.per:.1f}배 — 이익 대비 낮은 밸류에이션"))
        elif v.per > 40:
            c.append(("neg", f"PER {v.per:.1f}배 — 높은 성장 기대가 반영된 고평가 구간"))
        else:
            c.append(("neu", f"PER {v.per:.1f}배"))
    if v.pbr is not None:
        if v.pbr < 1:
            c.append(("pos", f"PBR {v.pbr:.2f}배 — 순자산가치 이하에서 거래"))
        elif v.pbr > 5:
            c.append(("neg", f"PBR {v.pbr:.2f}배 — 순자산 대비 높은 프리미엄"))
    if v.roe is not None:
        if v.roe >= 15:
            c.append(("pos", f"ROE {v.roe:.1f}% — 우수한 자본 효율성"))
        elif v.roe < 0:
            c.append(("neg", f"ROE {v.roe:.1f}% — 순손실로 자본 감소 중"))
    if v.debt_ratio is not None and v.debt_ratio > 200:
        c.append(("neg", f"부채비율 {v.debt_ratio:.0f}% — 재무 레버리지 높음"))
    return v
