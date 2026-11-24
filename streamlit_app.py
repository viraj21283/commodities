"""
Commodities Futures Dashboard (Streamlit) - entrypoint
Run:  streamlit run app.py
"""

import streamlit as st

from common import TIMEZONES

st.set_page_config(page_title="Commodities Futures Dashboard", page_icon="📈", layout="wide")

with st.sidebar:
    st.selectbox("Display timezone", TIMEZONES, key="tz")
    st.caption("Used for quote times, news and event times. Compare them with your broker "
               "or exchange to check data freshness.")

pages = st.navigation([
    st.Page("dashboard.py", title="Dashboard", icon="📈", default=True),
    st.Page("news.py", title="News", icon="📰"),
    st.Page("events.py", title="Events Calendar", icon="🗓️"),
])
pages.run()

st.divider()
st.caption(
    "**Disclaimer:** This dashboard is for informational and educational purposes only and "
    "does not constitute investment, trading, financial or legal advice, or a recommendation "
    "or solicitation to buy or sell any security, commodity or derivative. Price data is "
    "sourced from Yahoo Finance and may be delayed, incomplete or inaccurate. News headlines "
    "are aggregated from third-party publishers and are not verified or endorsed. Event dates, "
    "times and importance ratings are compiled from public schedules and rule-based judgments, "
    "may change without notice, and are not guaranteed. Technical ratings are simplified, "
    "rule-based calculations and are not predictions of future performance. Futures and "
    "commodities trading involves substantial risk of loss and is not suitable for every "
    "investor. Verify all prices and event details with your broker or the official source "
    "before making any trading decision. Past performance is not indicative of future results."
)