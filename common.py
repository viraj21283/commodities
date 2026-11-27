"""Shared configuration and helpers for all pages."""

import re
from zoneinfo import ZoneInfo

import streamlit as st

TIMEZONES = ["Asia/Kolkata", "UTC", "America/New_York", "Europe/London"]

# name   : label shown everywhere
# symbol : label shown in the dashboard table
# yf     : Yahoo Finance ticker (continuous front-month)
# ccy    : currency symbol shown before prices
# cme/root/exch/months : CME/NYMEX product; the active contract month is auto-detected
#          (highest recent volume). Set "quote": "SIZ26.CMX" to pin a contract instead.
# dec    : decimals shown
# unit   : quoted unit
# news   : Google News search query used on the News page
INSTRUMENTS = [
    {"name": "Gold", "symbol": "GC", "yf": "GC=F", "cme": True, "root": "GC", "exch": "CMX", "months": [2, 4, 6, 8, 10, 12], "ccy": "$", "dec": 2,
     "unit": "USD per troy oz", "news": 'gold price OR "gold futures"'},
    {"name": "Silver", "symbol": "SI", "yf": "SI=F", "cme": True, "root": "SI", "exch": "CMX", "months": [1, 3, 5, 7, 9, 12], "ccy": "$", "dec": 3,
     "unit": "USD per troy oz", "news": 'silver price OR "silver futures"'},
    {"name": "WTI Crude", "symbol": "CL", "yf": "CL=F", "cme": True, "root": "CL", "exch": "NYM", "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "ccy": "$", "dec": 2,
     "unit": "USD per barrel", "news": '"WTI crude" OR "crude oil prices"'},
    {"name": "Brent Crude", "symbol": "BZ", "yf": "BZ=F", "cme": True, "root": "BZ", "exch": "NYM", "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "ccy": "$", "dec": 2,
     "unit": "USD per barrel", "news": '"Brent crude" OR "Brent oil"'},
    {"name": "Natural Gas", "symbol": "NG", "yf": "NG=F", "cme": True, "root": "NG", "exch": "NYM", "months": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "ccy": "$", "dec": 3,
     "unit": "USD per MMBtu", "news": '"natural gas futures" OR "natural gas prices"'},
    {"name": "Copper", "symbol": "HG", "yf": "HG=F", "cme": True, "root": "HG", "exch": "CMX", "months": [3, 5, 7, 9, 12], "ccy": "$", "dec": 4,
     "unit": "USD per lb", "news": '"copper price" OR "copper futures"'},
    {"name": "Dollar Index (DXY)", "symbol": "DX", "yf": "DX-Y.NYB", "ccy": "", "dec": 3,
     "unit": "index points", "news": '"dollar index" OR DXY'},
    {"name": "USD/INR", "symbol": "USDINR", "yf": "INR=X", "ccy": "₹", "dec": 4,
     "unit": "INR per USD, spot", "news": '"USD/INR" OR "Indian rupee" OR rupee'},
]


def tz() -> ZoneInfo:
    """Display timezone chosen in the sidebar."""
    return ZoneInfo(st.session_state.get("tz", TIMEZONES[0]))


def esc_md(text: str) -> str:
    """Escape characters that Streamlit's markdown would treat as formatting (incl. $ for LaTeX)."""
    return re.sub(r"([\\`*_\[\]$<>~|#])", r"\\\1", text or "")
