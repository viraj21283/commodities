"""Dashboard page: live futures prices and technicals."""

from zoneinfo import ZoneInfo

import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st
import yfinance as yf

from common import INSTRUMENTS, tz

ET = ZoneInfo("America/New_York")
SMA_WINDOWS = (20, 89, 200)
RSI_PERIOD = 14
DATA_TTL = 3600              # prices and bars are downloaded at most once an hour
UI_REFRESH_SECONDS = 60      # cheap re-render (no downloads) so a new hourly update shows up
TECH_REFRESH_SECONDS = 300   # technicals table re-render
STALE_MINUTES = 30           # flag data that was already this old when it was downloaded

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
def _retry(fn):
    """Run fn() up to twice; return None if it keeps failing."""
    for _ in range(2):
        try:
            return fn()
        except Exception:
            continue
    return None


def _pmap(fn, items):
    """Run fn over items in parallel threads (fn must not call Streamlit)."""
    items = list(items)
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(fn, items))


def _close_series(h) -> pd.Series:
    if h is not None and not h.empty and "Close" in h:
        return h["Close"].dropna()
    return pd.Series(dtype=float)


def _dl_daily_max(t: str) -> pd.Series:
    return _close_series(_retry(
        lambda: yf.Ticker(t).history(period="max", interval="1d", auto_adjust=False)))


def _dl_daily_recent(t: str) -> pd.Series:
    return _close_series(_retry(
        lambda: yf.Ticker(t).history(period="10d", interval="1d", auto_adjust=False)))


def _dl_hourly(t: str) -> pd.Series:
    return _close_series(_retry(
        lambda: yf.Ticker(t).history(period="60d", interval="60m", auto_adjust=False)))


def _dl_intraday(t: str) -> dict:
    """Recent 1-minute bars (tz-aware index) plus Yahoo's chart metadata (live price etc.)."""
    tk = yf.Ticker(t)
    for period in ("2d", "5d"):
        h = _retry(lambda: tk.history(period=period, interval="1m", auto_adjust=False))
        if h is not None and not h.empty and "Close" in h:
            h = h.dropna(subset=["Close"])
            if not h.empty:
                if h.index.tz is None:
                    h.index = h.index.tz_localize("UTC")
                try:
                    meta = dict(tk.history_metadata or {})
                except Exception:
                    meta = {}
                return {"bars": h[["Open", "High", "Low", "Close"]], "meta": meta}
    return {"bars": pd.DataFrame(), "meta": {}}


def _dl_fast(t: str) -> dict:
    try:
        return _fast_info_quote(t)
    except Exception:
        return {"ltp": None, "prev": None, "open": None, "high": None, "low": None}


# Each batch fetches every instrument in parallel (one round trip instead of eight in a row),
# so a refresh takes about a second and can never queue up behind slow requests.
@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def batch_daily(tickers: tuple) -> dict:
    return dict(zip(tickers, _pmap(_dl_daily_max, tickers)))


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def batch_hourly(tickers: tuple) -> dict:
    return dict(zip(tickers, _pmap(_dl_hourly, tickers)))


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def batch_recent_daily(tickers: tuple) -> dict:
    return dict(zip(tickers, _pmap(_dl_daily_recent, tickers)))


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def batch_intraday(tickers: tuple) -> dict:
    out = dict(zip(tickers, _pmap(_dl_intraday, tickers)))
    out["_fetched_at"] = pd.Timestamp.now(tz="UTC")      # when this download happened
    return out


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def batch_fast(tickers: tuple) -> dict:
    return dict(zip(tickers, _pmap(_dl_fast, tickers)))


def clear_quote_cache():
    """Manual refresh: forget downloaded prices so the next render downloads fresh data."""
    batch_intraday.clear()
    batch_fast.clear()
    batch_hourly.clear()
    batch_recent_daily.clear()


def last_refreshed():
    """Timestamp (UTC) of the last download of live prices."""
    return batch_intraday(quote_tickers()).get("_fetched_at")


def series_tickers() -> tuple:
    return tuple(i["yf"] for i in INSTRUMENTS)


def quote_tickers() -> tuple:
    return tuple(quote_ticker(i) for i in INSTRUMENTS)


def non_cme_tickers() -> tuple:
    return tuple(quote_ticker(i) for i in INSTRUMENTS if not i.get("cme"))


def _from(batch, tickers: tuple, t: str, single):
    data = batch(tickers)
    return data[t] if t in data else single(t)


def get_daily(t: str) -> pd.Series:
    return _from(batch_daily, series_tickers(), t, _dl_daily_max)


def get_hourly(t: str) -> pd.Series:
    return _from(batch_hourly, series_tickers(), t, _dl_hourly)


def get_recent_daily(t: str) -> pd.Series:
    return _from(batch_recent_daily, quote_tickers(), t, _dl_daily_recent)


def get_intraday(t: str) -> dict:
    return _from(batch_intraday, quote_tickers(), t, _dl_intraday)


def get_fast(t: str) -> dict:
    return _from(batch_fast, non_cme_tickers(), t, _dl_fast)


MONTH_CODES = "FGHJKMNQUVXZ"
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _num(v):
    try:
        return None if v is None or pd.isna(v) else float(v)
    except Exception:
        return None


def _fast_info_quote(ticker: str) -> dict:
    """Yahoo's own quote fields (calendar-day based), with fallback to daily history."""
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
        h = t.history(period="5d", interval="1d", auto_adjust=False)
        h = h.dropna(subset=["Close"]) if "Close" in h else h.iloc[0:0]
        if len(h) >= 1:
            last = h.iloc[-1]
            prev = h.iloc[-2] if len(h) >= 2 else last
            q["ltp"] = q["ltp"] if pd.notna(q["ltp"]) else last["Close"]
            q["prev"] = q["prev"] if pd.notna(q["prev"]) else prev["Close"]
            q["open"] = q["open"] if pd.notna(q["open"]) else last["Open"]
            q["high"] = q["high"] if pd.notna(q["high"]) else last["High"]
            q["low"] = q["low"] if pd.notna(q["low"]) else last["Low"]
    return {k: _num(v) for k, v in q.items()}


# ---------------------------------------------------------------------------
# Contract detection: Yahoo's continuous tickers (GC=F, SI=F ...) can point at a different
# contract month than the one other sites show. We price the most actively traded month.
# ---------------------------------------------------------------------------
def candidate_tickers(inst: dict, n: int = 5) -> list:
    now = pd.Timestamp.now(tz="UTC")
    y, m, out = now.year, now.month, []
    while len(out) < n:
        if m in inst["months"]:
            out.append(f"{inst['root']}{MONTH_CODES[m - 1]}{y % 100:02d}.{inst['exch']}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _volume_info(ticker: str):
    try:
        h = yf.Ticker(ticker).history(period="10d", interval="1d", auto_adjust=False)
        h = h.dropna(subset=["Close"])
        if h.empty:
            return None
        vols = h["Volume"].fillna(0)
        recent = vols.iloc[-4:-1] if len(vols) >= 2 else vols.iloc[-1:]
        return {"last": h.index[-1].date(), "vol": float(recent.mean())}
    except Exception:
        return None


def _contract_label(ticker: str) -> str:
    code, yy = ticker[2], ticker.split(".")[0][3:]
    return f"{MONTH_NAMES[MONTH_CODES.index(code)]} {yy}"


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def resolve_contracts() -> dict:
    """{instrument name: {ticker, symbol, label, detail}} for CME/NYMEX products."""
    jobs = {i["name"]: candidate_tickers(i) for i in INSTRUMENTS if i.get("root")}
    flat = sorted({t for ts in jobs.values() for t in ts})
    if not flat:
        return {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        info = dict(zip(flat, ex.map(_volume_info, flat)))

    out = {}
    for name, cands in jobs.items():
        found = [(t, info[t]) for t in cands if info.get(t)]
        if not found:
            continue
        newest = max(i["last"] for _, i in found)
        live = [(t, i) for t, i in found if (newest - i["last"]).days <= 1]
        best = max(live, key=lambda x: x[1]["vol"])      # ties -> nearest month
        out[name] = {
            "ticker": best[0],
            "symbol": best[0].split(".")[0],
            "label": _contract_label(best[0]),
            "detail": ", ".join(f"{t.split('.')[0]}: {i['vol']:,.0f}" for t, i in live),
        }
    return out


def quote_ticker(inst: dict) -> str:
    """Ticker used for live quote values: a pinned contract ("quote" in common.py), else the
    auto-detected active contract, else Yahoo's continuous front-month ticker."""
    if inst.get("quote"):
        return inst["quote"]
    if inst.get("root"):
        c = resolve_contracts().get(inst["name"])
        if c:
            return c["ticker"]
    return inst["yf"]


def display_symbol(inst: dict) -> str:
    qt = quote_ticker(inst)
    return qt.split(".")[0] if inst.get("root") and qt != inst["yf"] else inst["symbol"]


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
def _session_values(bars: pd.DataFrame, ticker: str):
    """Open/High/Low of the current exchange session (6 PM ET to 5 PM ET) and the previous
    trading day's settlement (daily-bar close)."""
    last_ts = bars.index[-1].tz_convert(ET)
    start = last_ts.replace(hour=18, minute=0, second=0, microsecond=0)
    if last_ts < start:
        start = start - pd.DateOffset(days=1)
    sess = bars[bars.index >= start]
    if sess.empty:
        sess = bars.tail(1)
    trade_date = (start + pd.DateOffset(days=1)).date()

    out = {"open": float(sess["Open"].iloc[0]), "high": float(sess["High"].max()),
           "low": float(sess["Low"].min()), "session_start": start}
    daily = get_recent_daily(ticker)
    if len(daily):
        mask = daily.index.date < trade_date
        if mask.any():
            out["prev"] = float(daily[mask].iloc[-1])
    return out


def get_quote(ticker: str, cme: bool = False) -> dict:
    """Latest quote (all underlying downloads are cached for DATA_TTL).

    LTP is Yahoo's live price. For CME/NYMEX futures the previous close is the prior
    settlement and the day's range spans the whole exchange session (union of 1-minute bars
    and Yahoo's own day high/low). DXY and USD/INR use Yahoo's quote fields as-is."""
    data = get_intraday(ticker)
    bars, meta = data["bars"], data["meta"]

    bar_ts = bars.index[-1] if not bars.empty else None
    meta_ts = None
    if _num(meta.get("regularMarketTime")) is not None:
        meta_ts = pd.Timestamp(int(meta["regularMarketTime"]), unit="s", tz="UTC")
    stamps = [t for t in (bar_ts, meta_ts) if t is not None]

    meta_px = _num(meta.get("regularMarketPrice"))
    meta_hi = _num(meta.get("regularMarketDayHigh"))
    meta_lo = _num(meta.get("regularMarketDayLow"))
    meta_prev = _num(meta.get("chartPreviousClose"))
    if meta_prev is None:
        meta_prev = _num(meta.get("previousClose"))
    bar_ltp = float(bars["Close"].iloc[-1]) if not bars.empty else None

    q = {"session_start": None, "last_bar": max(stamps) if stamps else None,
         "bar_ltp": bar_ltp}

    if cme:
        ltp = meta_px if meta_px is not None else bar_ltp
        prev, opn, hi, lo = meta_prev, None, meta_hi, meta_lo
        if ltp is None or prev is None:                  # rare: fall back to Yahoo's quote
            fb = get_fast(ticker)
            ltp = ltp if ltp is not None else fb["ltp"]
            prev = prev if prev is not None else fb["prev"]
            opn, hi, lo = fb["open"], hi if hi is not None else fb["high"], \
                lo if lo is not None else fb["low"]
        q.update({"fi_ltp": meta_px, "fi_prev": meta_prev, "fi_high": meta_hi,
                  "fi_low": meta_lo, "src": "Yahoo live price"})
        if not bars.empty:
            try:
                s = _session_values(bars, ticker)
            except Exception:
                s = None
            if s:
                highs = [v for v in (s["high"], hi, ltp) if v is not None]
                lows = [v for v in (s["low"], lo, ltp) if v is not None]
                hi, lo, opn = max(highs), min(lows), s["open"]
                q["session_start"] = s["session_start"]
                q["src"] = "Yahoo live price + exchange-session range/settlement"
                if s.get("prev") is not None:
                    prev = s["prev"]
        q.update({"ltp": ltp, "prev": prev, "open": opn, "high": hi, "low": lo})
    else:
        raw = get_fast(ticker)
        ltp = raw["ltp"] if raw["ltp"] is not None else (meta_px if meta_px is not None
                                                          else bar_ltp)
        q.update({"ltp": ltp, "prev": raw["prev"], "open": raw["open"], "high": raw["high"],
                  "low": raw["low"], "fi_ltp": raw["ltp"], "fi_prev": raw["prev"],
                  "fi_high": raw["high"], "fi_low": raw["low"], "src": "Yahoo fast_info"})
    return q


@st.cache_data(ttl=21600, show_spinner=False)
def get_contract_name(ticker: str) -> str:
    """Contract Yahoo is pricing, e.g. 'Silver Dec 26'."""
    try:
        info = yf.Ticker(ticker).info
        return info.get("shortName") or info.get("longName") or "—"
    except Exception:
        return "—"


def get_snapshot(inst: dict) -> dict:
    q = get_quote(quote_ticker(inst), bool(inst.get("cme", False)))
    snap = {k: q[k] for k in ("ltp", "prev", "open", "high", "low")}
    if snap["ltp"] is None or snap["prev"] is None:
        raise ValueError(f"Yahoo returned no price data for {inst['yf']}")
    return snap


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
def money(v, ccy, dec=2):
    return "—" if v is None or pd.isna(v) else f"{ccy}{v:,.{dec}f}"


def signed_money(v, ccy, dec=2):
    if v is None or pd.isna(v):
        return "—"
    return f"-{ccy}{abs(v):,.{dec}f}" if v < 0 else f"{ccy}{v:,.{dec}f}"


def fmt_time(ts, zone: ZoneInfo, ref=None) -> str:
    """Local time of a data point; flagged when it was already stale at download time."""
    if ts is None:
        return "—"
    local = ts.tz_convert(zone)
    age_min = ((ref if ref is not None else pd.Timestamp.now(tz="UTC")) - ts).total_seconds() / 60
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
                "qtime": get_quote(quote_ticker(inst), bool(inst.get("cme", False)))["last_bar"],
            })
        except Exception as e:
            st.warning(f"Could not fetch {inst['name']} ({inst['yf']}): {e}")

    if not rows:
        return None, None

    disp, rsi_num, chg_num = [], [], []
    for r in rows:
        c = r["inst"]["ccy"]
        d = r["inst"].get("dec", 2)
        disp.append({
            "Name": r["inst"]["name"],
            "Symbol": display_symbol(r["inst"]),
            "Previous Close": money(r["prev"], c, d),
            "LTP": money(r["ltp"], c, d),
            "Change": signed_money(r["change"], c, d),
            "% Change": f"{r['pct']:.2f}%",
            "Open": money(r["open"], c, d),
            "Day High": money(r["high"], c, d),
            "Day Low": money(r["low"], c, d),
            "20-SMA": money(r["sma20"], c, d),
            "89-SMA": money(r["sma89"], c, d),
            "200-SMA": money(r["sma200"], c, d),
            "Daily RSI": "—" if r["rsi"] is None else f"{r['rsi']:.1f}",
            time_col: fmt_time(r["qtime"], zone, last_refreshed()),
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


@st.fragment(run_every=UI_REFRESH_SECONDS)
def live_table():
    try:
        disp, nums = build_main_table()
    except Exception:
        st.warning("Prices could not be loaded just now; retrying automatically.")
        return
    if disp is None:
        st.error("No data available right now.")
        return
    st.dataframe(style_main(disp, nums), hide_index=True, width="stretch")
    st.button("🔄 Refresh now", key="refresh_now", on_click=clear_quote_cache)

    zone = tz()
    fetched = last_refreshed()
    if fetched is not None:
        asof = fetched.tz_convert(zone)
        nxt = (fetched + pd.Timedelta(seconds=DATA_TTL)).tz_convert(zone)
        st.caption(
            f"Prices downloaded {asof:%d %b %H:%M:%S} {asof.tzname()} · updates automatically "
            f"every hour (next around {nxt:%H:%M}) or press Refresh now · "
            f"⚠ = Yahoo's last quote was already older than {STALE_MINUTES} min when "
            "downloaded (market may be closed)"
        )
    units = " · ".join(f"{i['name']}: {i.get('unit', '')}" for i in INSTRUMENTS)
    st.caption(
        f"Front-month futures (USD/INR is the spot rate). Units: {units}. "
        "For commodity futures, Open/High/Low cover the current exchange session (from "
        "6 PM ET the evening before) and Previous Close is the prior settlement price. "
        "SMAs/RSI use daily closes with today's bar set to LTP. "
        "RSI tint: green ≥ 60, red ≤ 40. Data: Yahoo Finance (itself delayed about 10+ "
        "minutes), downloaded hourly, so prices can be over an hour behind live quotes "
        "elsewhere."
    )

    if st.toggle("Show data check (compare with Investing.com or your broker)",
                 key="data_check"):
        rows = []
        for inst in INSTRUMENTS:
            try:
                qt = quote_ticker(inst)
                q = get_quote(qt, bool(inst.get("cme", False)))
                c, dec = inst["ccy"], inst.get("dec", 2)
                last = q["last_bar"]
                ref = last_refreshed()
                age = ("—" if last is None or ref is None else
                       f"{(ref - last).total_seconds() / 60:.0f} min (at download)")
                rows.append({
                    "Name": inst["name"],
                    "Yahoo ticker": qt,
                    "Contract priced": get_contract_name(qt),
                    "LTP": money(q["ltp"], c, dec),
                    "Yahoo raw LTP": money(q["fi_ltp"], c, dec),
                    "Previous close used": money(q["prev"], c, dec),
                    "Yahoo raw prev close": money(q["fi_prev"], c, dec),
                    "Open": money(q["open"], c, dec),
                    "High": money(q["high"], c, dec),
                    "Yahoo raw High": money(q["fi_high"], c, dec),
                    "Low": money(q["low"], c, dec),
                    "Yahoo raw Low": money(q["fi_low"], c, dec),
                    "Session start": ("—" if q["session_start"] is None else
                                      f"{q['session_start'].tz_convert(zone):%d %b %H:%M} "
                                      f"{pd.Timestamp.now(tz=zone).tzname()}"),
                    "1-min bar LTP": money(q["bar_ltp"], c, dec),
                    "Last data point": fmt_time(last, zone, last_refreshed()),
                    "Age": age,
                    "Method": q["src"],
                    "Contract choice (avg daily volume)": (
                        (resolve_contracts().get(inst["name"]) or {}).get("detail", "continuous ticker")
                        if not inst.get("quote") else "pinned"),
                })
            except Exception as e:
                rows.append({"Name": inst["name"], "Yahoo ticker": quote_ticker(inst),
                             "Contract priced": f"error: {e}"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(
            "Check that 'Contract priced' is the same contract month you are comparing "
            "against (e.g. Silver Dec 26 = SIZ6), and that 'Age' is small. Values from "
            "different times or contracts will not match."
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
        cfg = next(i for i in INSTRUMENTS if i["name"] == pick)
        ccy, dec = cfg["ccy"], cfg.get("dec", 2)
        detail = []
        for tf in TIMEFRAMES:
            a = results[pick].get(tf)
            if not a:
                detail.append({"Timeframe": TF_LABELS[tf], "Rating": "N/A"})
                continue
            detail.append({
                "Timeframe": TF_LABELS[tf],
                "Price": money(a["Price"], ccy, dec),
                f"RSI({RSI_PERIOD})": "—" if a["RSI"] is None else f"{a['RSI']:.1f}",
                "20-SMA": money(a["SMA20"], ccy, dec),
                "89-SMA": money(a["SMA89"], ccy, dec),
                "200-SMA": money(a["SMA200"], ccy, dec),
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
