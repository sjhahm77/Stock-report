"""Plotly 인터랙티브 차트.

색상 규칙: 국내 관례에 따라 상승=빨강, 하락=파랑. 이동평균선은 상승/하락색과 겹치지 않는 범주색을 고정 순서로 사용한다.
가격·거래량·보조지표는 y축을 섞지 않고 패널(서브플롯)을 분리한다.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .analysis import add_indicators

UP, DOWN = "#e0383e", "#2a78d6"
MA_COLORS = {5: "#eb6834", 20: "#1baf7a", 60: "#eda100", 120: "#e87ba4"}
MUTED = "#8a8a85"
GRID = "rgba(128,128,128,0.15)"


def _missing_business_days(idx: pd.DatetimeIndex) -> list[str]:
    """휴장일(평일인데 데이터가 없는 날)을 x축에서 숨기기 위한 목록."""
    if len(idx) == 0:
        return []
    all_bd = pd.bdate_range(idx.min(), idx.max())
    return [d.strftime("%Y-%m-%d") for d in all_bd.difference(idx)]


def price_chart(df: pd.DataFrame, name: str, indicator: str = "RSI", show_bb: bool = False) -> go.Figure:
    df = add_indicators(df)
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06, row_heights=[0.6, 0.18, 0.22],
        subplot_titles=("", "거래량", indicator),
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=name,
            increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
            decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
            hoverlabel=dict(namelength=0),
        ),
        row=1, col=1,
    )
    for w, color in MA_COLORS.items():
        fig.add_trace(
            go.Scatter(x=df.index, y=df[f"ma{w}"], name=f"MA{w}", line=dict(color=color, width=1.5),
                       hovertemplate=f"MA{w}: %{{y:,.0f}}<extra></extra>"),
            row=1, col=1,
        )
    if show_bb:
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_upper"], name="볼린저 상단", line=dict(color=MUTED, width=1, dash="dot"),
                                 hovertemplate="BB상단: %{y:,.0f}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_lower"], name="볼린저 하단", line=dict(color=MUTED, width=1, dash="dot"),
                                 fill="tonexty", fillcolor="rgba(138,138,133,0.08)",
                                 hovertemplate="BB하단: %{y:,.0f}<extra></extra>"), row=1, col=1)

    vol_colors = [UP if c >= o else DOWN for o, c in zip(df["open"], df["close"])]
    fig.add_trace(
        go.Bar(x=df.index, y=df["volume"], name="거래량", marker=dict(color=vol_colors, line=dict(width=0)), opacity=0.7,
               hovertemplate="거래량: %{y:,.0f}주<extra></extra>", showlegend=False),
        row=2, col=1,
    )
    fig.add_trace(go.Scatter(x=df.index, y=df["vol_ma20"], name="거래량 MA20", line=dict(color=MUTED, width=1.2),
                             hovertemplate="거래량 MA20: %{y:,.0f}<extra></extra>", showlegend=False), row=2, col=1)

    if indicator == "MACD":
        hist_colors = [UP if v >= 0 else DOWN for v in df["macd_hist"].fillna(0)]
        fig.add_trace(go.Bar(x=df.index, y=df["macd_hist"], marker=dict(color=hist_colors, line=dict(width=0)), name="MACD Hist",
                             showlegend=False, hovertemplate="Hist: %{y:,.1f}<extra></extra>"), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["macd"], name="MACD", line=dict(color="#eb6834", width=1.5),
                                 showlegend=False, hovertemplate="MACD: %{y:,.1f}<extra></extra>"), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["macd_signal"], name="Signal", line=dict(color="#1baf7a", width=1.5),
                                 showlegend=False, hovertemplate="Signal: %{y:,.1f}<extra></extra>"), row=3, col=1)
    else:
        fig.add_trace(go.Scatter(x=df.index, y=df["rsi"], name="RSI(14)", line=dict(color="#eb6834", width=1.5),
                                 showlegend=False, hovertemplate="RSI: %{y:.1f}<extra></extra>"), row=3, col=1)
        for lvl in (30, 70):
            fig.add_hline(y=lvl, line=dict(color=MUTED, width=1, dash="dot"), row=3, col=1)
        fig.update_yaxes(range=[0, 100], row=3, col=1)

    # 기본 표시 구간: 최근 6개월 (전체 기간은 레인지 버튼으로)
    start = max(df.index[0], df.index[-1] - pd.DateOffset(months=6))
    fig.update_xaxes(
        rangebreaks=[dict(bounds=["sat", "mon"]), dict(values=_missing_business_days(df.index))],
        showgrid=False, range=[start, df.index[-1] + pd.Timedelta(days=1)],
    )
    fig.update_xaxes(
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1M", step="month", stepmode="backward"),
                dict(count=3, label="3M", step="month", stepmode="backward"),
                dict(count=6, label="6M", step="month", stepmode="backward"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(step="all", label="전체"),
            ],
            x=0, y=1.06,
        ),
        rangeslider=dict(visible=False),
        row=1, col=1,
    )
    fig.update_yaxes(gridcolor=GRID, tickformat=",", zeroline=False)
    fig.update_layout(
        height=720, margin=dict(l=10, r=10, t=40, b=10), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis_rangeslider_visible=False, dragmode="pan",
    )
    return fig


def relative_chart(close: pd.Series, index: pd.Series, name: str) -> go.Figure:
    """종목 vs 코스닥 지수, 시작일=100 기준 정규화 (단일 y축)."""
    joined = pd.concat([close, index.reindex(close.index).ffill()], axis=1, keys=["s", "m"], sort=True).dropna()
    fig = go.Figure()
    if joined.empty:
        return fig
    base = joined.iloc[0]
    norm = joined / base * 100
    fig.add_trace(go.Scatter(x=norm.index, y=norm["s"], name=name, line=dict(color="#2a78d6", width=2),
                             hovertemplate=f"{name}: %{{y:.1f}}<extra></extra>"))
    fig.add_trace(go.Scatter(x=norm.index, y=norm["m"], name="코스닥 지수", line=dict(color=MUTED, width=2),
                             hovertemplate="코스닥: %{y:.1f}<extra></extra>"))
    fig.add_hline(y=100, line=dict(color=MUTED, width=1, dash="dot"))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], showgrid=False)
    fig.update_yaxes(gridcolor=GRID, title="기준일=100")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    return fig
