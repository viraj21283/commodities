"""News page: latest headlines for the commodities being tracked."""

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd
import requests
import streamlit as st

from common import INSTRUMENTS, esc_md, tz

NEWS_TTL = 300               # seconds between feed refreshes
MAX_PER_COMMODITY = 15
MAX_SHOWN = 60
FEED_URL = "https://news.google.com/rss/search"
LOOKBACKS = {"Last 24 hours": "1d", "Last 3 days": "3d", "Last 7 days": "7d"}


@st.cache_data(ttl=NEWS_TTL, show_spinner=False)
def fetch_news(query: str, lookback: str) -> list[dict]:
    """Latest headlines for one search query (Google News RSS)."""
    resp = requests.get(
        FEED_URL,
        params={"q": f"{query} when:{lookback}", "hl": "en-US", "gl": "US", "ceid": "US:en"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        src = item.find("source")
        source = (src.text or "").strip() if src is not None else ""
        # Google appends " - Publisher" to the headline
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].rstrip()

        ts = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                ts = pd.Timestamp(parsedate_to_datetime(pub))
                ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            except Exception:
                ts = None
        items.append({"title": title, "link": link, "source": source, "ts": ts})

    epoch = pd.Timestamp(0, tz="UTC")
    items.sort(key=lambda x: x["ts"] if x["ts"] is not None else epoch, reverse=True)
    return items[:MAX_PER_COMMODITY]


def collect(selected: tuple, lookback: str):
    """Fetch, merge (same story under several commodities) and sort headlines."""
    merged: dict[str, dict] = {}
    errors = []
    for inst in INSTRUMENTS:
        if inst["name"] not in selected:
            continue
        try:
            items = fetch_news(inst["news"], LOOKBACKS[lookback])
        except Exception as e:
            errors.append(f"{inst['name']}: {e}")
            continue
        for it in items:
            key = it["title"].lower()
            if key in merged:
                if inst["name"] not in merged[key]["tags"]:
                    merged[key]["tags"].append(inst["name"])
            else:
                merged[key] = {**it, "tags": [inst["name"]]}

    epoch = pd.Timestamp(0, tz="UTC")
    stories = sorted(merged.values(), key=lambda x: x["ts"] if x["ts"] is not None else epoch,
                     reverse=True)
    return stories, errors


def ago(ts) -> str:
    mins = (pd.Timestamp.now(tz="UTC") - ts).total_seconds() / 60
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{int(mins)}m ago"
    if mins < 48 * 60:
        return f"{int(mins // 60)}h ago"
    return f"{int(mins // 1440)}d ago"


@st.fragment(run_every=NEWS_TTL)
def news_feed(selected: tuple, lookback: str, keyword: str):
    stories, errors = collect(selected, lookback)

    if keyword:
        k = keyword.lower()
        stories = [s for s in stories if k in s["title"].lower() or k in s["source"].lower()]

    for err in errors:
        st.warning(f"Could not load news for {err}")

    if not stories:
        if not errors:
            st.info("No headlines found for this selection. Try a longer time window.")
        return

    zone = tz()
    st.caption(f"{min(len(stories), MAX_SHOWN)} headlines · newest first · "
               f"times in {pd.Timestamp.now(tz=zone).tzname()} · auto-refreshes every "
               f"{NEWS_TTL // 60} min")

    for s in stories[:MAX_SHOWN]:
        link = s["link"].replace(")", "%29").replace(" ", "%20")
        with st.container(border=True):
            st.markdown(f"**[{esc_md(s['title'])}]({link})**")
            bits = []
            if s["source"]:
                bits.append(esc_md(s["source"]))
            if s["ts"] is not None:
                local = s["ts"].tz_convert(zone)
                bits.append(f"{ago(s['ts'])} ({local:%d %b, %H:%M} {local.tzname()})")
            bits.append(" · ".join(s["tags"]))
            st.caption(" · ".join(bits))


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Commodities News")

names = [i["name"] for i in INSTRUMENTS]
c1, c2, c3 = st.columns([3, 1.3, 1.7])
selected = c1.multiselect("Commodities", names, default=names)
lookback = c2.selectbox("Time window", list(LOOKBACKS), index=1)
keyword = c3.text_input("Filter headlines", placeholder="e.g. Fed, OPEC, tariffs")
st.button("Refresh now", on_click=fetch_news.clear)

if not selected:
    st.info("Select at least one commodity.")
else:
    news_feed(tuple(selected), lookback, keyword.strip())

st.caption(
    "Headlines are aggregated automatically from third-party publishers via Google News. "
    "They are not verified or endorsed here; open the original article for full context."
)
