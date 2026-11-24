"""Events calendar page: scheduled events that move commodities, ranked by importance."""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

from common import INSTRUMENTS, esc_md, tz

ET = ZoneInfo("America/New_York")

VI, IMP, NOT = "Very Important", "Important", "Not that important"
BADGE = {VI: "🔴", IMP: "🟠", NOT: "⚪"}
IMP_STYLE = {VI: "color: #d93025; font-weight: 700", IMP: "color: #e8710a; font-weight: 600",
             NOT: "color: #8a8f98"}

FEED_URLS = {
    "this week": "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "next week": "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
}
FEED_COUNTRIES = {"USD", "CNY", "EUR", "All"}
FEED_TTL = 1800
RECURRING_HORIZON_DAYS = 45

NAMES = [i["name"] for i in INSTRUMENTS]


def pick(*wanted):
    return [n for n in wanted if n in NAMES]


GOLD_SILVER = pick("Gold", "Silver")
OIL = pick("WTI Crude", "Brent Crude")
GAS = pick("Natural Gas")
COPPER = pick("Copper")
ALL = list(NAMES)
FX = pick("Dollar Index (DXY)", "USD/INR")

# ---------------------------------------------------------------------------
# Classification rules for events from the live feed (first match wins).
# pat       : regex on the event title (case-insensitive)
# countries : currency codes the rule applies to (None = any)
# key       : used to de-duplicate against the curated list below
# ---------------------------------------------------------------------------
RULES = [
    dict(pat=r"\badp\b", countries={"USD"}, key="adp", imp=IMP, affects=GOLD_SILVER + FX,
         why="Private payrolls estimate that previews the official jobs report; a big miss or "
             "beat can shift rate expectations."),
    dict(pat=r"federal funds rate|fomc statement|fomc economic projections", countries={"USD"},
         key="fomc_decision", imp=VI, affects=ALL,
         why="The Fed's rate decision and guidance steer the US dollar and real yields, the "
             "biggest driver of gold and silver and a key input for every dollar-priced commodity."),
    dict(pat=r"fomc press conference", countries={"USD"}, key="fomc_presser", imp=VI, affects=ALL,
         why="The Fed Chair's tone on future rate moves often shifts gold, silver and the dollar "
             "more than the decision itself."),
    dict(pat=r"fomc meeting minutes|fomc minutes", countries={"USD"}, key="fomc_minutes",
         imp=IMP, affects=GOLD_SILVER + FX,
         why="Shows how divided the Fed was and hints at the future rate path; moves the dollar "
             "and gold if the tone differs from expectations."),
    dict(pat=r"non-?farm", countries={"USD"}, key="nfp", imp=VI, affects=ALL,
         why="The jobs report reshapes Fed rate expectations and can swing the dollar, gold and "
             "silver within minutes."),
    dict(pat=r"\bcpi\b", countries={"USD"}, key="cpi", imp=VI, affects=ALL,
         why="Inflation prints drive Fed policy bets and gold's inflation-hedge demand; a "
             "surprise moves the dollar, metals and energy."),
    dict(pat=r"core pce|pce price", countries={"USD"}, key="pce", imp=IMP, affects=GOLD_SILVER + FX,
         why="The Fed's preferred inflation gauge; shapes rate expectations and gold."),
    dict(pat=r"\bppi\b", countries={"USD"}, key=None, imp=IMP, affects=GOLD_SILVER + FX,
         why="Producer-price inflation is an early read on consumer inflation and Fed policy."),
    dict(pat=r"unemployment rate|average hourly earnings|participation rate", countries={"USD"},
         key=None, imp=IMP, affects=GOLD_SILVER + FX,
         why="Part of the labour data that shapes Fed rate expectations."),
    dict(pat=r"\bjolts\b|job openings", countries={"USD"}, key=None, imp=IMP,
         affects=GOLD_SILVER + FX,
         why="Job openings gauge labour demand, an input to Fed policy views."),
    dict(pat=r"\bgdp\b", countries={"USD"}, key=None, imp=IMP, affects=ALL,
         why="Growth data affects rate expectations and the demand outlook for industrial "
             "commodities."),
    dict(pat=r"retail sales", countries={"USD"}, key=None, imp=IMP, affects=ALL,
         why="Consumer spending is a major growth driver; surprises shift rate expectations and "
             "demand for oil and metals."),
    dict(pat=r"\bism\b", countries={"USD"}, key=None, imp=IMP, affects=COPPER + OIL + GOLD_SILVER + FX,
         why="US factory and services activity gauge; reflects industrial demand for copper and "
             "oil and sways Fed expectations."),
    dict(pat=r"crude oil inventories", countries={"USD"}, key="eia_crude", imp=IMP, affects=OIL,
         why="Weekly US crude and fuel stock changes are the most direct read on oil supply and "
             "demand; surprises move WTI and Brent."),
    dict(pat=r"natural gas storage", countries={"USD"}, key="eia_gas", imp=IMP, affects=GAS,
         why="The weekly storage build or draw versus expectations is the main short-term driver "
             "of natural gas prices."),
    dict(pat=r"beige book", countries={"USD"}, key="beige", imp=IMP, affects=GOLD_SILVER + FX,
         why="The Fed's anecdotal read on the economy; can nudge rate expectations ahead of FOMC "
             "meetings."),
    dict(pat=r"fed chair", countries={"USD"}, key=None, imp=IMP, affects=GOLD_SILVER + FX,
         why="Comments from the Fed Chair can shift rate expectations and move gold, silver and "
             "the dollar."),
    dict(pat=r"main refinancing rate|ecb press conference|monetary policy statement",
         countries={"EUR"}, key=None, imp=IMP, affects=GOLD_SILVER + FX,
         why="ECB policy drives EUR/USD, the dollar's largest component, and through it gold and "
             "dollar-priced commodities."),
    dict(pat=r"loan prime rate", countries={"CNY"}, key=None, imp=IMP, affects=COPPER + OIL,
         why="China's benchmark lending rate signals stimulus and credit conditions, which affect "
             "demand for metals and energy."),
    dict(pat=r"\bpmi\b", countries={"CNY"}, key=None, imp=IMP, affects=COPPER + OIL,
         why="China is the top buyer of copper and a leading oil importer; PMIs are a timely "
             "gauge of that demand."),
    dict(pat=r"industrial production|retail sales|trade balance|fixed asset|\bgdp\b",
         countries={"CNY"}, key=None, imp=IMP, affects=COPPER + OIL,
         why="Industrial output, retail sales and trade data show the strength of Chinese demand "
             "for metals and energy."),
    dict(pat=r"inflation|\bcpi\b|\bhicp\b", countries=None, key=None, imp=IMP,
         affects=GOLD_SILVER + FX,
         why="Inflation data abroad shifts central-bank expectations and currencies, which spill "
             "over to commodities."),
]

FALLBACK_HOLIDAY = (NOT, [], "Market holiday: thinner liquidity and possible shifts in related "
                              "data release times.")
FALLBACK_HIGH = (IMP, [], "Flagged high-impact by the calendar source; can move the dollar and "
                          "risk sentiment, which feed into commodities.")
FALLBACK_LOW = (NOT, [], "Lower-impact release; usually limited effect on commodities unless "
                         "it surprises sharply.")


def classify(title: str, country: str, impact: str):
    """Returns (key, importance, affects, why)."""
    for r in RULES:
        if (r["countries"] is None or country in r["countries"]) and re.search(
                r["pat"], title, re.I):
            return r["key"], r["imp"], r["affects"], r["why"]
    if impact == "Holiday":
        return (None, *FALLBACK_HOLIDAY)
    if impact == "High":
        return (None, *FALLBACK_HIGH)
    return (None, *FALLBACK_LOW)


# ---------------------------------------------------------------------------
# Curated events (dates checked against official schedules on 24 Nov 2026).
# Times are US Eastern. Add your own entries here; `time=None` means all-day.
# ---------------------------------------------------------------------------
_R = {r["key"]: r for r in RULES if r["key"]}

CURATED = [
    dict(date="2026-11-25", time="14:00", title="Fed Beige Book", key="beige"),
    dict(date="2026-11-29", time=None, title="OPEC+ Meeting (7 core members)", key="opec",
         imp=VI, affects=OIL,
         why="OPEC+ output decisions set crude supply expectations; 2027 production policy is "
             "expected to be a focus, which can move WTI and Brent sharply."),
    dict(date="2026-12-04", time="08:30", title="US Non-Farm Payrolls (November)", key="nfp"),
    dict(date="2026-12-09", time="14:00", title="FOMC Rate Decision & Economic Projections",
         key="fomc_decision"),
    dict(date="2026-12-09", time="14:30", title="FOMC Press Conference", key="fomc_presser"),
    dict(date="2026-12-30", time="14:00", title="FOMC Minutes (Dec 8-9 meeting)",
         key="fomc_minutes"),
    dict(date="2027-01-08", time="08:30", title="US Non-Farm Payrolls (December)", key="nfp"),
    dict(date="2027-01-13", time="08:30", title="US CPI (December)", key="cpi"),
    dict(date="2027-01-13", time="14:00", title="Fed Beige Book", key="beige"),
    dict(date="2027-01-27", time="14:00", title="FOMC Rate Decision (Jan 26-27, tentative)",
         key="fomc_decision"),
    dict(date="2027-03-17", time="14:00", title="FOMC Rate Decision (Mar 16-17, tentative)",
         key="fomc_decision"),
    dict(date="2027-04-28", time="14:00", title="FOMC Rate Decision (Apr 27-28, tentative)",
         key="fomc_decision"),
    dict(date="2027-06-09", time="14:00", title="FOMC Rate Decision (Jun 8-9, tentative)",
         key="fomc_decision"),
]

# ---------------------------------------------------------------------------
# Typical weekly releases (used only beyond the live feed's coverage)
# weekday: Monday=0 ... Sunday=6; times are US Eastern
# ---------------------------------------------------------------------------
RECURRING = [
    dict(weekday=2, time="10:30", title="EIA Weekly Petroleum Status Report", imp=IMP,
         affects=OIL, why=_R["eia_crude"]["why"]),
    dict(weekday=3, time="10:30", title="EIA Natural Gas Storage Report", imp=IMP,
         affects=GAS, why=_R["eia_gas"]["why"]),
    dict(weekday=4, time="13:00", title="Baker Hughes US Rig Count", imp=NOT,
         affects=OIL + GAS,
         why="Rig counts hint at future US oil and gas supply; slow-moving and rarely a catalyst."),
    dict(weekday=4, time="15:30", title="CFTC Commitments of Traders", imp=NOT, affects=ALL,
         why="Weekly positioning data shows how speculators are placed; useful context, rarely "
             "a catalyst."),
]
NO_RELEASE_DATES = {date(2026, 12, 25), date(2027, 1, 1)}   # US holidays on a Friday


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------
@st.cache_data(ttl=FEED_TTL, show_spinner=False)
def fetch_feed():
    """Raw events from the free weekly macro-calendar feed (this week + next week)."""
    events, errors = [], []
    for label, url in FEED_URLS.items():
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            r.raise_for_status()
            events.extend(r.json())
        except Exception as e:
            errors.append(f"{label}: {e}")
    return events, errors


def make_event(**kw) -> dict:
    base = dict(start=None, et_date=None, all_day=False, title="", importance=NOT, why="",
                affects=[], forecast="", previous="", source="", key=None)
    base.update(kw)
    return base


def parse_feed(raw: list):
    """Returns (events to show, last date covered by the feed)."""
    shown, last = [], None
    for r in raw:
        try:
            parsed = pd.Timestamp(r["date"])
        except Exception:
            continue
        impact = r.get("impact", "")
        country = r.get("country", "")
        title = r.get("title", "").strip()
        local_date = parsed.date()
        ts = parsed.tz_localize("UTC") if parsed.tzinfo is None else parsed.tz_convert("UTC")
        et_date = ts.tz_convert(ET).date()
        all_day = impact == "Holiday"
        last = max(last, local_date if all_day else et_date) if last else (
            local_date if all_day else et_date)

        if country not in FEED_COUNTRIES:
            continue
        key, imp, affects, why = classify(title, country, impact)
        shown.append(make_event(
            start=None if all_day else ts,
            et_date=local_date if all_day else et_date,
            all_day=all_day,
            title=f"{title} ({country})" if country not in ("USD", "All") else title,
            importance=imp, why=why, affects=affects,
            forecast=str(r.get("forecast") or ""), previous=str(r.get("previous") or ""),
            source="Live macro feed", key=key,
        ))
    return shown, last


def curated_events() -> list:
    out = []
    for c in CURATED:
        rule = _R.get(c["key"], {})
        imp = c.get("imp", rule.get("imp", IMP))
        affects = c.get("affects", rule.get("affects", []))
        why = c.get("why", rule.get("why", ""))
        d = datetime.strptime(c["date"], "%Y-%m-%d").date()
        if c["time"]:
            naive = datetime.strptime(f"{c['date']} {c['time']}", "%Y-%m-%d %H:%M")
            ts = pd.Timestamp(naive).tz_localize(ET).tz_convert("UTC")
        else:
            ts = None
        out.append(make_event(start=ts, et_date=d, all_day=ts is None, title=c["title"],
                              importance=imp, why=why, affects=affects,
                              source="Official schedule", key=c["key"]))
    return out


def recurring_events(start: date, end: date) -> list:
    out = []
    d = start
    while d <= end:
        if d not in NO_RELEASE_DATES:
            for r in RECURRING:
                if d.weekday() == r["weekday"]:
                    naive = datetime.strptime(f"{d} {r['time']}", "%Y-%m-%d %H:%M")
                    ts = pd.Timestamp(naive).tz_localize(ET).tz_convert("UTC")
                    out.append(make_event(
                        start=ts, et_date=d, title=r["title"], importance=r["imp"],
                        why=r["why"], affects=r["affects"],
                        source="Typical weekly schedule (may shift around holidays)"))
        d += timedelta(days=1)
    return out


def build_events():
    raw, errors = fetch_feed()
    feed, coverage_end = parse_feed(raw)
    curated = curated_events()

    curated_keys = {(e["key"], e["et_date"]) for e in curated if e["key"]}
    feed = [e for e in feed if not (e["key"] and (e["key"], e["et_date"]) in curated_keys)]

    today_et = pd.Timestamp.now(tz=ET).date()
    rec_start = max(today_et, coverage_end + timedelta(days=1)) if coverage_end else today_et
    recurring = recurring_events(rec_start, today_et + timedelta(days=RECURRING_HORIZON_DAYS))

    return feed + curated + recurring, errors


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def localize(events: list, zone: ZoneInfo) -> list:
    """Adds display date/time in the chosen timezone and a sort key."""
    out = []
    for e in events:
        if e["all_day"]:
            d, t_str, sort_min, et_str = e["et_date"], "All day", -1, ""
        else:
            local = e["start"].tz_convert(zone)
            d = local.date()
            t_str = f"{local:%H:%M} {local.tzname()}"
            sort_min = local.hour * 60 + local.minute
            et = e["start"].tz_convert(ET)
            et_str = f"{et:%H:%M} {et.tzname()}"
        out.append({**e, "disp_date": d, "disp_time": t_str, "sort": (d, sort_min),
                    "et_time": et_str})
    out.sort(key=lambda x: x["sort"])
    return out


def in_range(d: date, start: date, end):
    return d >= start and (end is None or d <= end)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Events Calendar")

zone = tz()
tzname = pd.Timestamp.now(tz=zone).tzname()
today = pd.Timestamp.now(tz=zone).date()

f1, f2, f3 = st.columns([2.2, 1.3, 2])
importance_pick = f1.radio("Importance", ["All", VI, IMP, NOT], horizontal=True)
range_pick = f2.selectbox("Date range", ["Today", "Next 7 days", "Next 30 days", "All upcoming"],
                          index=1)
affects_pick = f3.multiselect("Affects", NAMES, default=NAMES)
view = st.segmented_control("View", ["Table", "Agenda"], default="Table") or "Table"

events, feed_errors = build_events()
events = localize(events, zone)

end = {"Today": today, "Next 7 days": today + timedelta(days=6),
       "Next 30 days": today + timedelta(days=29), "All upcoming": None}[range_pick]
ranged = [e for e in events if in_range(e["disp_date"], today, end)]

if set(affects_pick) != set(NAMES):
    ranged = [e for e in ranged if set(e["affects"]) & set(affects_pick)]

counts = {k: sum(1 for e in ranged if e["importance"] == k) for k in (VI, IMP, NOT)}
st.caption(f"{BADGE[VI]} {counts[VI]} very important · {BADGE[IMP]} {counts[IMP]} important · "
           f"{BADGE[NOT]} {counts[NOT]} not that important · times in {tzname}")

shown = [e for e in ranged if importance_pick in ("All", e["importance"])]

if feed_errors:
    st.warning("Live macro feed unavailable for: " + "; ".join(feed_errors) +
               ". Showing official-schedule events and typical weekly releases only.")

if not shown:
    st.info("No events match these filters.")
elif view == "Table":
    df = pd.DataFrame([{
        "Date": f"{e['disp_date']:%a %d %b %Y}",
        "Time": e["disp_time"],
        "Importance": f"{BADGE[e['importance']]} {e['importance']}",
        "Event": e["title"],
        "Affects": ", ".join(e["affects"]) if e["affects"] else "Broad market",
        "Why it matters": e["why"],
        "Forecast": e["forecast"],
        "Previous": e["previous"],
        "Source": e["source"],
    } for e in shown])
    # st.table wraps long text so every cell is fully visible
    styled = df.style.apply(
        lambda _col: [IMP_STYLE[e["importance"]] for e in shown], subset=["Importance"])
    try:
        st.table(styled, hide_index=True)
    except TypeError:   # older Streamlit without hide_index
        st.table(styled.hide(axis="index"))
else:
    current = None
    for e in shown:
        if e["disp_date"] != current:
            current = e["disp_date"]
            label = "Today" if current == today else f"{current:%A, %d %B %Y}"
            st.markdown(f"#### {label}")
        st.markdown(f"{BADGE[e['importance']]} **{esc_md(e['disp_time'])} · "
                    f"{esc_md(e['title'])}**  \n*Why it matters:* {esc_md(e['why'])}")
        meta = [e["importance"],
                "Affects: " + (", ".join(e["affects"]) if e["affects"] else "Broad market")]
        if e["forecast"] or e["previous"]:
            meta.append(f"Forecast: {e['forecast'] or '—'} · Previous: {e['previous'] or '—'}")
        if e["et_time"]:
            meta.append(e["et_time"])
        meta.append(e["source"])
        st.caption(" | ".join(esc_md(m) for m in meta))

st.caption(
    "Sources: live macro calendar feed (this week and next, USD/EUR/CNY events), official "
    "Federal Reserve, BLS and OPEC schedules (hand-checked on 24 Nov 2026), and typical weekly "
    "commodity releases beyond the feed's range. Importance and 'why it matters' are rule-based "
    "judgments, not official ratings. Dates and times can change; confirm with the official "
    "source before trading."
)