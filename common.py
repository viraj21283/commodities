"""Shared configuration and helpers for all pages."""

import re
from zoneinfo import ZoneInfo

import streamlit as st

TIMEZONES = ["Asia/Kolkata", "UTC", "America/New_York", "Europe/London"]

# name   : label shown everywhere
# symbol : label shown in the dashboard table
# yf     : Yahoo Finance ticker (continuous front-month)
# ccy    : currency symbol (all instruments are in USD)
# unit   : quoted unit
# news   : Google News search query used on the News page
INSTRUMENTS = [
    {"name": "Gold", "symbol": "GC", "yf": "GC=F", "ccy": "$", "unit": "troy oz",
     "news": 'gold price OR "gold futures"'},
    {"name": "Silver", "symbol": "SI", "yf": "SI=F", "ccy": "$", "unit": "troy oz",
     "news": 'silver price OR "silver futures"'},
    {"name": "WTI Crude", "symbol": "CL", "yf": "CL=F", "ccy": "$", "unit": "barrel",
     "news": '"WTI crude" OR "crude oil prices"'},
    {"name": "Brent Crude", "symbol": "BZ", "yf": "BZ=F", "ccy": "$", "unit": "barrel",
     "news": '"Brent crude" OR "Brent oil"'},
    {"name": "Natural Gas", "symbol": "NG", "yf": "NG=F", "ccy": "$", "unit": "MMBtu",
     "news": '"natural gas futures" OR "natural gas prices"'},
    {"name": "Copper", "symbol": "HG", "yf": "HG=F", "ccy": "$", "unit": "lb",
     "news": '"copper price" OR "copper futures"'},
]


def tz() -> ZoneInfo:
    """Display timezone chosen in the sidebar."""
    return ZoneInfo(st.session_state.get("tz", TIMEZONES[0]))


def esc_md(text: str) -> str:
    """Escape characters that Streamlit's markdown would treat as formatting (incl. $ for LaTeX)."""
    return re.sub(r"([\\`*_\[\]$<>~|#])", r"\\\1", text or "")
