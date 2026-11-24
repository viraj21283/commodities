"""News page: headlines from top sources that commodity traders follow."""

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st

from common import INSTRUMENTS, esc_md, tz

NEWS_TTL = 300               # seconds between feed refreshes
MAX_PER_COMMODITY = 15
MAX_SHOWN = 60
GOOGLE_NEWS = "https://news.google.com/rss/search"
GOLDSILVER_FEED = "https://goldsilver.com/industry-news/rss/"
LOOKBACKS = {"Last 24 hours": "1d", "Last 3 days": "3d", "Last 7 days": "7d"}
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Only headlines from these publishers are shown (domain -> label).
# Edit this list to add or remove sources.
TRUSTED_SOURCES = {
    "goldsilver.com": "GoldSilver",
    "kitco.com": "Kitco",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times",
    "wsj.com": "WSJ",
    "cnbc.com": "CNBC",
    "marketwatch.com": "MarketWatch",
    "barrons.com": "Barron's",
    "investing.com": "Investing.com",
    "mining.com": "Mining.com",
    "gold.org": "World Gold Council",
    "bullionvault.com": "BullionVault",
    "oilprice.com": "OilPrice.com",
    "rigzone.com": "Rigzone",
    "naturalgasintel.com": "NGI",
    "spglobal.com": "S&P Global",
    "argusmedia.com": "Argus Media",
    "fastmarkets.com": "Fastmarkets",
    "eia.gov": "U.S. EIA",
}
SITE_CLAUSE = " OR ".join(f"site:{d}" for d in TRUSTED_SOURCES)

# Used to tag GoldSilver.com articles (read directly from its RSS feed)
COMMODITY_PATTERNS = {
    "Gold": r"\bgold\b",
    "Silver": r"\bsilver\b",
    "WTI Crude": r"\bwti\b|\bcrude\b|\boil\b",
    "Brent Crude": r"\bbrent\b|\bcrude\b|\boil\b",
    "Natural Gas": r"natural gas|\blng\b",
    "Copper": r"\bcopper\b",
}

EPOCH = pd.Timestamp(0, tz="UTC")


def parse_ts(pub):
    if not pub:
        return None
    try:
        ts = pd.Timestamp(parsedate_to_datetime(pub))
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    except Exception:
        return None


def trusted_label(url: str):
    """Label of the trusted source that owns this URL, else None."""
    domain = urlparse(url).netloc.lower().removeprefix("www.") if url else ""
    for d, label in TRUSTED_SOURCES.items():
        if domain == d or domain.endswith("." + d):
            return label
    return None


def cutoff(lookback: str) -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=int(LOOKBACKS[lookback][:-1]))


@st.cache_data(ttl=NEWS_TTL, show_spinner=False)
def fetch_news(query: str, lookback: str) -> list[dict]:
    """Headlines for one search query, restricted to trusted publishers (Google News RSS)."""
    resp = requests.get(
        GOOGLE_NEWS,
        params={"q": f"({query}) ({SITE_CLAUSE}) when:{LOOKBACKS[lookback]}",
                "hl": "en-US", "gl": "US", "ceid": "US:en"},
        headers=HEADERS, timeout=10,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        src = item.find("source")
        label = trusted_label(src.get("url", "") if src is not None else "")
        if not title or not link or label is None:
            continue   # drop anything not from a trusted publisher
        publisher = (src.text or "").strip()
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -(len(publisher) + 3)].rstrip()
        items.append({"title": title, "link": link, "source": label,
                      "ts": parse_ts(item.findtext("pubDate"))})

    items.sort(key=lambda x: x["ts"] if x["ts"] is not None else EPOCH, reverse=True)
    return items[:MAX_PER_COMMODITY]


@st.cache_data(ttl=NEWS_TTL, show_spinner=False)
def fetch_goldsilver() -> list[dict]:
    """Latest articles straight from GoldSilver.com's own RSS feed."""
    resp = requests.get(GOLDSILVER_FEED, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        items.append({"title": title, "link": link, "source": "GoldSilver",
                      "ts": parse_ts(item.findtext("pubDate")), "text": f"{title} {desc}"})
    return items


def goldsilver_tags(text: str) -> list[str]:
    names = {i["name"] for i in INSTRUMENTS}
    tags = [n for n, p in COMMODITY_PATTERNS.items() if n in names and re.search(p, text, re.I)]
    # GoldSilver is a precious-metals publisher: default macro pieces to gold and silver
    return tags or [n for n in ("Gold", "Silver") if n in names]


def collect(selected: tuple, lookback: str):
    """Fetch, merge (same story under several commodities) and sort headlines."""
    merged: dict[str, dict] = {}
    errors = []
    limit = cutoff(lookback)

    def add(item, tag):
        key = item["title"].lower()
        if key in merged:
            if tag not in merged[key]["tags"]:
                merged[key]["tags"].append(tag)
        else:
            merged[key] = {"title": item["title"], "link": item["link"],
                           "source": item["source"], "ts": item["ts"], "tags": [tag]}

    for inst in INSTRUMENTS:
        if inst["name"] not in selected:
            continue
        try:
            for it in fetch_news(inst["news"], lookback):
                add(it, inst["name"])
        except Exception as e:
            errors.append(f"{inst['name']} (Google News): {e}")

    try:
        for it in fetch_goldsilver():
            if it["ts"] is not None and it["ts"] < limit:
                continue
            for tag in goldsilver_tags(it["text"]):
                if tag in selected:
                    add(it, tag)
    except Exception as e:
        errors.append(f"GoldSilver.com feed: {e}")

    stories = sorted(merged.values(), key=lambda x: x["ts"] if x["ts"] is not None else EPOCH,
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
def news_feed(selected: tuple, lookback: str, keyword: str, sources: tuple):
    stories, errors = collect(selected, lookback)
    stories = [s for s in stories if s["source"] in sources]

    if keyword:
        k = keyword.lower()
        stories = [s for s in stories if k in s["title"].lower() or k in s["source"].lower()]

    for err in errors:
        st.warning(f"Could not load news for {err}")

    if not stories:
        if not errors:
            st.info("No headlines found for this selection. Try a longer time window or more "
                    "sources.")
        return

    zone = tz()
    st.caption(f"{min(len(stories), MAX_SHOWN)} headlines · newest first · "
               f"times in {pd.Timestamp.now(tz=zone).tzname()} · auto-refreshes every "
               f"{NEWS_TTL // 60} min")

    for s in stories[:MAX_SHOWN]:
        link = s["link"].replace(")", "%29").replace(" ", "%20")
        with st.container(border=True):
            st.markdown(f"**[{esc_md(s['title'])}]({link})**")
            bits = [f"**{esc_md(s['source'])}**"]
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

with st.expander(f"Sources ({len(TRUSTED_SOURCES)} trusted publishers)"):
    sources = st.multiselect("Show headlines from", list(TRUSTED_SOURCES.values()),
                             default=list(TRUSTED_SOURCES.values()))
st.button("Refresh now", on_click=lambda: (fetch_news.clear(), fetch_goldsilver.clear()))

if not selected:
    st.info("Select at least one commodity.")
elif not sources:
    st.info("Select at least one source.")
else:
    news_feed(tuple(selected), lookback, keyword.strip(), tuple(sources))

st.caption(
    "Only headlines from a curated list of publishers that commodity traders follow are shown. "
    "GoldSilver.com is read directly from its own news feed; the others come via Google News "
    "search. Headlines are not verified or endorsed here; open the original article for full "
    "context."
)