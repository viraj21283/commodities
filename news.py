"""News page: headlines from top sources that commodity traders follow."""

import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st

from common import INSTRUMENTS, esc_md, tz

NEWS_TTL = 600               # seconds a fetched result stays fresh
MIN_GAP = 0.7                # seconds between outgoing requests (avoid rate limits)
FAIL_COOLDOWN = 180          # after a failed fetch, wait this long before retrying
MAX_PER_COMMODITY = 15
MAX_SHOWN = 60
GOOGLE_NEWS = "https://news.google.com/rss/search"
ZEROHEDGE_FEEDS = [
    "https://cms.zerohedge.com/fullrss2.xml",
    "https://feeds.feedburner.com/zerohedge/feed",
]
LOOKBACKS = {"Last 24 hours": "1d", "Last 3 days": "3d", "Last 7 days": "7d"}
HEADERS = {"User-Agent": "Mozilla/5.0"}
log = logging.getLogger(__name__)

# Only headlines from these publishers are shown (domain -> label).
# Edit this list to add or remove sources.
TRUSTED_SOURCES = {
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
    "zerohedge.com": "ZeroHedge",
}
SITE_CLAUSE = " OR ".join(f"site:{d}" for d in TRUSTED_SOURCES)

# Used to tag ZeroHedge articles (read directly from its RSS feed; it covers all topics,
# so only articles matching a tracked instrument are kept)
COMMODITY_PATTERNS = {
    "Gold": r"\bgold\b",
    "Silver": r"\bsilver\b",
    "WTI Crude": r"\bwti\b|\bcrude\b|\boil\b",
    "Brent Crude": r"\bbrent\b|\bcrude\b|\boil\b",
    "Natural Gas": r"natural gas|\blng\b",
    "Copper": r"\bcopper\b",
    "Dollar Index (DXY)": r"dollar index|\bdxy\b|\bgreenback\b|\bdollar\b",
    "USD/INR": r"\busd/inr\b|\busdinr\b|\brupee\b|\binr\b",
}

EPOCH = pd.Timestamp(0, tz="UTC")


def parse_ts(pub):
    if not pub:
        return None
    try:
        ts = pd.Timestamp(parsedate_to_datetime(pub))
    except Exception:
        try:
            ts = pd.Timestamp(pub)
        except Exception:
            return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def trusted_label(url: str):
    """Label of the trusted source that owns this URL, else None."""
    domain = urlparse(url).netloc.lower().removeprefix("www.") if url else ""
    for d, label in TRUSTED_SOURCES.items():
        if domain == d or domain.endswith("." + d):
            return label
    return None


def cutoff(lookback: str) -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=int(LOOKBACKS[lookback][:-1]))


@st.cache_resource
def _store() -> dict:
    """Shared across sessions: cached results, failure cooldowns and a request lock."""
    return {"data": {}, "ts": {}, "fail_until": {}, "last_request": 0.0, "lock": threading.Lock()}


def clear_news_cache():
    s = _store()
    with s["lock"]:
        s["ts"].clear()
        s["fail_until"].clear()


def cached_fetch(key, fn) -> list:
    """Rate-limit-friendly fetch: serves fresh cache, spaces out requests, and on any
    failure quietly falls back to the last good result (never raises)."""
    s = _store()
    with s["lock"]:
        now = time.time()
        if key in s["data"] and now - s["ts"].get(key, 0) < NEWS_TTL:
            return s["data"][key]
        if now < s["fail_until"].get(key, 0):          # recently failed: don't hammer
            return s["data"].get(key, [])
        wait = MIN_GAP - (now - s["last_request"])
        if wait > 0:
            time.sleep(wait)
        try:
            data = fn()
        except Exception as e:
            log.warning("news fetch failed for %s: %s", key, e)
            s["fail_until"][key] = time.time() + FAIL_COOLDOWN
            return s["data"].get(key, [])               # stale data if we have it
        finally:
            s["last_request"] = time.time()
        s["data"][key] = data
        s["ts"][key] = time.time()
        s["fail_until"].pop(key, None)
        return data


def _download_news(query: str, lookback: str) -> list[dict]:
    """Headlines for one search query, restricted to trusted publishers (Google News RSS)."""
    params = {"q": f"({query}) ({SITE_CLAUSE}) when:{LOOKBACKS[lookback]}",
              "hl": "en-US", "gl": "US", "ceid": "US:en"}
    for attempt in range(2):
        resp = requests.get(GOOGLE_NEWS, params=params, headers=HEADERS, timeout=10)
        if resp.status_code in (429, 503) and attempt == 0:
            time.sleep(2.5)                              # brief back-off, then one retry
            continue
        resp.raise_for_status()
        break
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


def fetch_news(query: str, lookback: str) -> list[dict]:
    return cached_fetch(("google", query, lookback), lambda: _download_news(query, lookback))


def _download_zerohedge() -> list[dict]:
    """Latest articles straight from ZeroHedge's own RSS feed."""
    last_error = None
    for url in ZEROHEDGE_FEEDS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            last_error = e
            continue
        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link:
                continue
            desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
            items.append({"title": title, "link": link, "source": "ZeroHedge",
                          "ts": parse_ts(item.findtext("pubDate")), "text": f"{title} {desc}"})
        return items
    raise last_error or RuntimeError("no feed available")


def fetch_zerohedge() -> list[dict]:
    return cached_fetch(("zerohedge",), _download_zerohedge)


def direct_tags(text: str) -> list[str]:
    names = {i["name"] for i in INSTRUMENTS}
    return [n for n, p in COMMODITY_PATTERNS.items() if n in names and re.search(p, text, re.I)]


def collect(selected: tuple, lookback: str):
    """Fetch, merge (same story under several commodities) and sort headlines."""
    merged: dict[str, dict] = {}
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
        if inst["name"] in selected:
            for it in fetch_news(inst["news"], lookback):
                add(it, inst["name"])

    for it in fetch_zerohedge():
        if it["ts"] is not None and it["ts"] < limit:
            continue
        for tag in direct_tags(it["text"]):
            if tag in selected:
                add(it, tag)

    return sorted(merged.values(), key=lambda x: x["ts"] if x["ts"] is not None else EPOCH,
                  reverse=True)


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
    stories = collect(selected, lookback)
    stories = [s for s in stories if s["source"] in sources]

    if keyword:
        k = keyword.lower()
        stories = [s for s in stories if k in s["title"].lower() or k in s["source"].lower()]

    if not stories:
        st.info("No headlines available for this selection right now. Try a longer time window "
                "or more sources; this page retries automatically every few minutes.")
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
st.button("Refresh now", on_click=clear_news_cache)

if not selected:
    st.info("Select at least one commodity.")
elif not sources:
    st.info("Select at least one source.")
else:
    news_feed(tuple(selected), lookback, keyword.strip(), tuple(sources))

st.caption(
    "Only headlines from a curated list of publishers that commodity traders follow are shown. "
    "ZeroHedge is read directly from its own news feed (it is an opinionated, contrarian "
    "blog, so treat it as commentary); the others come via Google News search. Headlines are "
    "not verified or endorsed here; open the original article for full context."
)
