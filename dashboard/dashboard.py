import pandas as pd
import streamlit as st
import plotly.express as px

CSV_FILE = "trades.csv"

st.set_page_config(page_title="Trading Dashboard", layout="wide")
st.title("📊 Trading Bot Dashboard")

# ───────── LOAD DATA ─────────
@st.cache_data
def load_data():
    return pd.read_csv(CSV_FILE)

try:
    df = load_data()
except Exception as e:
    st.error(f"Error loading CSV: {e}")
    st.stop()

if df.empty:
    st.warning("No trades yet")
    st.stop()

# ───────── PREP DATA ─────────
df["time"] = pd.to_datetime(df["time"])
df = df.sort_values("time")

df["equity"] = df["net_pnl"].cumsum()
df["win"] = df["net_pnl"] > 0

# ───────── KPIs ─────────
col1, col2, col3, col4, col5 = st.columns(5)

col1.metric("Trades", len(df))
col2.metric("Winrate", f"{df['win'].mean()*100:.2f}%")
col3.metric("Net PnL", f"${df['net_pnl'].sum():.2f}")
col4.metric("Avg Trade", f"${df['net_pnl'].mean():.2f}")
col5.metric("Profit Factor",
    f"{abs(df[df.net_pnl>0].net_pnl.sum() / abs(df[df.net_pnl<0].net_pnl.sum())):.2f}" 
    if df[df.net_pnl<0].net_pnl.sum() != 0 else "∞"
)

# ───────── EQUITY CURVE ─────────
st.subheader("📈 Equity Curve")
fig_equity = px.line(df, x="time", y="equity")
st.plotly_chart(fig_equity, use_container_width=True)

# ───────── DRAWDOWN ─────────
df["peak"] = df["equity"].cummax()
df["drawdown"] = df["equity"] - df["peak"]

st.subheader("📉 Drawdown")
fig_dd = px.area(df, x="time", y="drawdown")
st.plotly_chart(fig_dd, use_container_width=True)

# ───────── DISTRIBUTIONS ─────────
col1, col2 = st.columns(2)

with col1:
    st.subheader("PnL Distribution")
    fig_hist = px.histogram(df, x="net_pnl", nbins=30)
    st.plotly_chart(fig_hist, use_container_width=True)

with col2:
    st.subheader("Trades by Symbol")
    fig_symbol = px.bar(df.groupby("symbol").net_pnl.sum().reset_index(),
        x="symbol", y="net_pnl")
    st.plotly_chart(fig_symbol, use_container_width=True)

# ───────── TABLE ─────────
st.subheader("📋 Trade Log")
st.dataframe(df[::-1], use_container_width=True)
