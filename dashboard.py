"""Dashboard page: live futures prices and technicals."""

from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yfinance as yf

from common import INSTRUMENTS, tz

SMA_WINDOWS = (20, 89, 200)
RSI_PERIOD = 14
REFRESH_SECONDS = 15         # main table
TECH_REFRESH_SECONDS = 60    # technicals table
STALE_MINUTES = 30           # flag quotes older than this

TIMEFRAMES = ["1H", "2H", "4H", "Daily", "Weekly", "Monthly"]
TF_LABELS = {"1H": "1 Hour", "2H": "2 Hours", "4H": "4 Hours",
             "Daily": "Daily", "Weekly": "Weekly", "Monthly": "Monthly"}

RATING_STYLE = {
    "Strong Buy":  "color: #0a7d3b; font-weight: 700",
    "Buy":         "color: #0a9e4b; font-weight: 600",
    "Neutral":     "color: #8a8f98",
    "Sell":        "color: #d93025; font-weight: 600",
    "Strong Sell": "color: #b00020; font-weight: 700",
}


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def get_daily(ticker: str) -> pd.Series:
    """Full daily history (needed for weekly/monthly 200-SMA)."""
    h = yf.Ticker(ticker).history(period="max", interval="1d", auto_adjust=False)
    return h["Close"].dropna()


@st.cache_data(ttl=120, show_spinner=False)
def get_hourly(ticker: str) -> pd.Series:
    """~60 days of hourly closes (2H and 4H are resampled from this)."""
    h = yf.Ticker(ticker).history(period="60d", interval="60m", auto_adjust=False)
    return h["Close"].dropna()


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def get_quote(ticker: str) -> dict:
    """Latest quote fields, with fallback to daily history."""
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


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner=False)
def get_quote_time(ticker: str):
    """Timestamp (tz-aware) of the most recent 1-minute bar, or None."""
    t = yf.Ticker(ticker)
    for period in ("1d", "5d"):
        try:
            h = t.history(period=period, interval="1m")
            if not h.empty:
                ts = h.index[-1]
                return ts.tz_localize("UTC") if ts.tzinfo is None else ts
        except Exception:
            continue
    return None


def get_snapshot(inst: dict) -> dict:
    q = get_quote(inst["yf"])
    return {k: float(q[k]) for k in ("ltp", "prev", "open", "high", "low")}


def get_series(ticker: str, tf: str) -> pd.Series:
    if tf in ("1H", "2H", "4H"):
        s = get_hourly(ticker)
        if tf != "1H":
            s = s.resample({"2H": "2h", "4H": "4h"}[tf]).last().dropna()
        return s
    d = get_daily(ticker)
    if tf == "Daily":
        return d
    if tf == "Weekly":
        return d.resample("W-FRI").last().dropna()
    if tf == "Monthly":
        return d.resample("ME").last().dropna()
    raise ValueError(tf)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------
def with_live(closes: pd.Series, ltp: float) -> pd.Series:
    """Set the in-progress bar's close to the live price."""
    s = closes.copy()
    if len(s):
        s.iloc[-1] = ltp
    return s


def calc_rsi(close: pd.Series, n: int = RSI_PERIOD) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def rate(score: float) -> str:
    if score >= 0.5:
        return "Strong Buy"
    if score >= 0.2:
        return "Buy"
    if score > -0.2:
        return "Neutral"
    if score > -0.5:
        return "Sell"
    return "Strong Sell"


def analyze(series: pd.Series, ltp: float) -> dict:
    """SMAs, RSI and a simple rating for one timeframe."""
    s = with_live(series, ltp)
    out = {"Price": ltp}
    for w in SMA_WINDOWS:
        out[f"SMA{w}"] = float(s.tail(w).mean()) if len(s) >= w else None

    r = calc_rsi(s).iloc[-1] if len(s) > RSI_PERIOD else float("nan")
    out["RSI"] = None if pd.isna(r) else float(r)

    signals = [(1 if ltp > out[f"SMA{w}"] else -1)
               for w in SMA_WINDOWS if out[f"SMA{w}"] is not None]
    if out["RSI"] is not None:
        signals.append(1 if out["RSI"] >= 60 else -1 if out["RSI"] <= 40 else 0)
    out["Rating"] = rate(sum(signals) / len(signals)) if signals else "N/A"
    return out


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def money(v, ccy):
    return "—" if v is None or pd.isna(v) else f"{ccy}{v:,.2f}"


def signed_money(v, ccy):
    if v is None or pd.isna(v):
        return "—"
    return f"-{ccy}{abs(v):,.2f}" if v < 0 else f"{ccy}{v:,.2f}"


def fmt_time(ts, zone: ZoneInfo) -> str:
    if ts is None:
        return "—"
    local = ts.tz_convert(zone)
    age_min = (pd.Timestamp.now(tz="UTC") - ts).total_seconds() / 60
    flag = " ⚠" if age_min > STALE_MINUTES else ""
    return f"{local:%d %b %H:%M:%S} {local.tzname()}{flag}"


# ---------------------------------------------------------------------------
# Main table
# ---------------------------------------------------------------------------
def build_main_table():
    zone = tz()
    time_col = f"Quote Time ({pd.Timestamp.now(tz=zone).tzname()})"
    rows = []
    for inst in INSTRUMENTS:
        try:
            snap = get_snapshot(inst)
            daily = get_series(inst["yf"], "Daily")
            a = analyze(daily, snap["ltp"])
            change = snap["ltp"] - snap["prev"]
            rows.append({
                "inst": inst,
                "prev": snap["prev"], "ltp": snap["ltp"], "change": change,
                "pct": change / snap["prev"] * 100 if snap["prev"] else float("nan"),
                "open": snap["open"], "high": snap["high"], "low": snap["low"],
                "sma20": a["SMA20"], "sma89": a["SMA89"], "sma200": a["SMA200"],
                "rsi": a["RSI"],
                "qtime": get_quote_time(inst["yf"]),
            })
        except Exception as e:
            st.warning(f"Could not fetch {inst['name']} ({inst['yf']}): {e}")

    if not rows:
        return None, None

    disp, rsi_num, chg_num = [], [], []
    for r in rows:
        c = r["inst"]["ccy"]
        disp.append({
            "Name": r["inst"]["name"],
            "Symbol": r["inst"]["symbol"],
            "Previous Close": money(r["prev"], c),
            "LTP": money(r["ltp"], c),
            "Change": signed_money(r["change"], c),
            "% Change": f"{r['pct']:.2f}%",
            "Open": money(r["open"], c),
            "Day High": money(r["high"], c),
            "Day Low": money(r["low"], c),
            "20-SMA": money(r["sma20"], c),
            "89-SMA": money(r["sma89"], c),
            "200-SMA": money(r["sma200"], c),
            "Daily RSI": "—" if r["rsi"] is None else f"{r['rsi']:.1f}",
            time_col: fmt_time(r["qtime"], zone),
        })
        rsi_num.append(r["rsi"] if r["rsi"] is not None else float("nan"))
        chg_num.append(r["change"])

    return pd.DataFrame(disp), {"rsi": rsi_num, "change": chg_num}


def style_main(disp: pd.DataFrame, nums: dict):
    def color_change(_col):
        return ["" if pd.isna(v) or v == 0
                else "color: #0a9e4b; font-weight: 600" if v > 0
                else "color: #d93025; font-weight: 600"
                for v in nums["change"]]

    def tint_rsi(_col):
        out = []
        for v in nums["rsi"]:
            if pd.isna(v):
                out.append("")
            elif v >= 60:
                out.append("background-color: rgba(10,158,75,0.18)")   # bullish momentum
            elif v <= 40:
                out.append("background-color: rgba(217,48,37,0.18)")   # bearish momentum
            else:
                out.append("")
        return out

    return (disp.style
            .apply(color_change, subset=["Change", "% Change"])
            .apply(tint_rsi, subset=["Daily RSI"]))


# ---------------------------------------------------------------------------
# Technicals table
# ---------------------------------------------------------------------------
def build_technicals() -> dict:
    results = {}
    for inst in INSTRUMENTS:
        try:
            snap = get_snapshot(inst)
        except Exception as e:
            st.warning(f"Could not fetch {inst['name']} ({inst['yf']}): {e}")
            continue
        per_tf = {}
        for tf in TIMEFRAMES:
            try:
                per_tf[tf] = analyze(get_series(inst["yf"], tf), snap["ltp"])
            except Exception:
                per_tf[tf] = None
        results[inst["name"]] = per_tf
    return results


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Commodities Futures Dashboard")


@st.fragment(run_every=REFRESH_SECONDS)
def live_table():
    disp, nums = build_main_table()
    if disp is None:
        st.error("No data available right now.")
        return
    st.dataframe(style_main(disp, nums), hide_index=True, width="stretch")

    zone = tz()
    now = pd.Timestamp.now(tz="UTC")
    clocks = " · ".join(
        f"{now.tz_convert(z):%H:%M:%S} {now.tz_convert(z).tzname()}"
        for z in (zone, ZoneInfo("UTC"), ZoneInfo("America/New_York"))
    )
    st.caption(
        f"Now: {clocks} · refreshes every {REFRESH_SECONDS}s · "
        f"⚠ = last quote older than {STALE_MINUTES} min (market may be closed)"
    )
    units = " · ".join(f"{i['name']}: USD per {i['unit']}" for i in INSTRUMENTS)
    st.caption(
        f"All prices in USD, front-month futures. Units: {units}. "
        "SMAs/RSI use daily closes with today's bar set to LTP. "
        "RSI tint: green ≥ 60, red ≤ 40. Data: Yahoo Finance (may be delayed)."
    )


@st.fragment(run_every=TECH_REFRESH_SECONDS)
def technicals():
    results = build_technicals()
    if not results:
        st.error("No technical data available right now.")
        return

    rows = []
    for name, per_tf in results.items():
        row = {"Name": name}
        for tf in TIMEFRAMES:
            a = per_tf.get(tf)
            row[TF_LABELS[tf]] = a["Rating"] if a else "N/A"
        rows.append(row)
    df = pd.DataFrame(rows)
    tf_cols = [TF_LABELS[t] for t in TIMEFRAMES]
    styled = df.style.map(lambda v: RATING_STYLE.get(v, ""), subset=tf_cols)
    st.dataframe(styled, hide_index=True, width="stretch")

    with st.expander("Indicator details (price, RSI and SMAs per timeframe)"):
        pick = st.selectbox("Instrument", list(results), key="detail_pick")
        ccy = next(i["ccy"] for i in INSTRUMENTS if i["name"] == pick)
        detail = []
        for tf in TIMEFRAMES:
            a = results[pick].get(tf)
            if not a:
                detail.append({"Timeframe": TF_LABELS[tf], "Rating": "N/A"})
                continue
            detail.append({
                "Timeframe": TF_LABELS[tf],
                "Price": money(a["Price"], ccy),
                f"RSI({RSI_PERIOD})": "—" if a["RSI"] is None else f"{a['RSI']:.1f}",
                "20-SMA": money(a["SMA20"], ccy),
                "89-SMA": money(a["SMA89"], ccy),
                "200-SMA": money(a["SMA200"], ccy),
                "Rating": a["Rating"],
            })
        st.dataframe(
            pd.DataFrame(detail).style.map(lambda v: RATING_STYLE.get(v, ""), subset=["Rating"]),
            hide_index=True, width="stretch",
        )

    st.caption(
        "Rating = average of signals: price vs 20/89/200-SMA (+1 above, −1 below) and "
        f"RSI({RSI_PERIOD}) (+1 if ≥ 60, −1 if ≤ 40, else 0). Score ≥ 0.5 Strong Buy, "
        "≥ 0.2 Buy, between −0.2 and 0.2 Neutral, ≤ −0.2 Sell, ≤ −0.5 Strong Sell. "
        "This is a simplified score, not TradingView's multi-indicator rating, so labels "
        "can differ. 2H/4H bars are resampled from Yahoo's 1H data (last ~60 days); "
        "Weekly/Monthly are resampled from daily history."
    )


live_table()
st.subheader("Technicals")
technicals()
