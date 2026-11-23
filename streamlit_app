"""
Live Futures Dashboard (Streamlit)
Run:  streamlit run app.py
"""

import datetime as dt

import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Futures Dashboard", layout="wide")

# ---------------------------------------------------------------------------
# Configuration: edit this list to add/remove instruments.
# `yf` is the Yahoo Finance ticker; `symbol` is the label shown in the table.
# Continuous front-month tickers (GC=F etc.) are used. For a specific contract
# use Yahoo's format, e.g. "GCZ26.CMX".
# ---------------------------------------------------------------------------
INSTRUMENTS = [
    {"name": "Gold",      "symbol": "GC", "yf": "GC=F", "ccy": "$"},
    {"name": "Silver",    "symbol": "SI", "yf": "SI=F", "ccy": "$"},
    {"name": "WTI Brent", "symbol": "CL", "yf": "CL=F", "ccy": "$"},
    {"name": "Natural Gas", "symbol": "NG", "yf": "NG=F", "ccy": "$"},
    {"name": "Copper",    "symbol": "HG", "yf": "HG=F", "ccy": "$"},  # use "₹" if you source MCX data
]

SMA_WINDOWS = (20, 89, 200)
REFRESH_SECONDS = 15


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def get_daily_closes(ticker: str) -> pd.Series:
    """~2 years of daily closes (enough for a 200-day SMA)."""
    hist = yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=False)
    return hist["Close"].dropna()


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def get_quote(ticker: str) -> dict:
    """Latest quote fields. Falls back to daily history if fast_info is missing data."""
    t = yf.Ticker(ticker)
    q = {"ltp": None, "prev": None, "open": None, "high": None, "low": None}
    try:
        fi = t.fast_info
        q["ltp"] = fi["last_price"]
        q["prev"] = fi["previous_close"]
        q["open"] = fi["open"]
        q["high"] = fi["day_high"]
        q["low"] = fi["day_low"]
    except Exception:
        pass

    if any(v is None or pd.isna(v) for v in q.values()):
        h = t.history(period="5d", interval="1d", auto_adjust=False).dropna(subset=["Close"])
        if len(h) >= 2:
            last, prev = h.iloc[-1], h.iloc[-2]
            q["ltp"] = q["ltp"] if pd.notna(q["ltp"]) else last["Close"]
            q["prev"] = q["prev"] if pd.notna(q["prev"]) else prev["Close"]
            q["open"] = q["open"] if pd.notna(q["open"]) else last["Open"]
            q["high"] = q["high"] if pd.notna(q["high"]) else last["High"]
            q["low"] = q["low"] if pd.notna(q["low"]) else last["Low"]
    return q


def sma(closes: pd.Series, ltp: float, window: int):
    """SMA of the last `window` daily closes, with today's bar set to the live price."""
    s = closes.copy()
    if len(s) == 0:
        return None
    s.iloc[-1] = ltp
    if len(s) < window:
        return None
    return float(s.tail(window).mean())


def build_table() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (numeric_df, display_df)."""
    rows = []
    for inst in INSTRUMENTS:
        try:
            q = get_quote(inst["yf"])
            closes = get_daily_closes(inst["yf"])
            ltp, prev = float(q["ltp"]), float(q["prev"])
            change = ltp - prev
            pct = change / prev * 100 if prev else float("nan")
            row = {
                "Name": inst["name"],
                "Symbol": inst["symbol"],
                "ccy": inst["ccy"],
                "Previous Close": prev,
                "LTP": ltp,
                "Change": change,
                "% Change": pct,
                "Open": float(q["open"]),
                "Day High": float(q["high"]),
                "Day Low": float(q["low"]),
            }
            for w in SMA_WINDOWS:
                row[f"{w}-SMA"] = sma(closes, ltp, w)
        except Exception as e:
            st.warning(f"Could not fetch {inst['name']} ({inst['yf']}): {e}")
            continue
        rows.append(row)

    num = pd.DataFrame(rows)
    if num.empty:
        return num, num

    price_cols = ["Previous Close", "LTP", "Open", "Day High", "Day Low"] + [
        f"{w}-SMA" for w in SMA_WINDOWS
    ]

    def money(v, ccy):
        return "—" if v is None or pd.isna(v) else f"{ccy}{v:,.2f}"

    disp = pd.DataFrame({"Name": num["Name"], "Symbol": num["Symbol"]})
    for c in price_cols:
        disp[c] = [money(v, ccy) for v, ccy in zip(num[c], num["ccy"])]
    disp.insert(
        4,
        "Change",
        [
            "—" if pd.isna(v) else f"-{ccy}{abs(v):,.2f}" if v < 0 else f"{ccy}{v:,.2f}"
            for v, ccy in zip(num["Change"], num["ccy"])
        ],
    )
    disp.insert(5, "% Change", [f"{v:.2f}%" for v in num["% Change"]])

    # keep the column order from the screenshot
    order = ["Name", "Symbol", "Previous Close", "LTP", "Change", "% Change",
             "Open", "Day High", "Day Low", "20-SMA", "89-SMA", "200-SMA"]
    return num, disp[order]


def style_table(num: pd.DataFrame, disp: pd.DataFrame):
    def color_change(col):
        out = []
        for v in num["Change"]:
            if pd.isna(v) or v == 0:
                out.append("")
            else:
                out.append("color: #0a9e4b; font-weight: 600" if v > 0
                           else "color: #d93025; font-weight: 600")
        return out

    return disp.style.apply(color_change, subset=["Change", "% Change"])


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Futures Dashboard")


@st.fragment(run_every=REFRESH_SECONDS)
def live_table():
    num, disp = build_table()
    if disp.empty:
        st.error("No data available right now.")
        return
    st.dataframe(style_table(num, disp), hide_index=True, use_container_width=True)
    st.caption(
        f"Last updated {dt.datetime.now().strftime('%H:%M:%S')} · refreshes every "
        f"{REFRESH_SECONDS}s · SMAs use daily closes with today's bar set to LTP · "
        "Data: Yahoo Finance (may be delayed)"
    )


live_table()
