import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import io
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- 1. 初始化與介面設定 ---
st.set_page_config(page_title="Truth 6.0 Master Pro Dashboard", layout="wide", page_icon="🧭")
st.title("🧭 真理 6.0：大師靈魂 x 4D 宏觀導航系統 (Master Pro)")
st.markdown("遵循 **三層作戰系統**：底層被動結構、中層年齡滑動路徑 (Glide Path)、頂層 4D 宏觀微調。")

# --- 2. 數據獲獲引擎 ---
@st.cache_data(ttl=3600)
def fetch_system_data():
    end = datetime.now()
    start = end - timedelta(days=730)
    tickers = ["VT", "0050.TW", "QQQ", "^GSPC", "^VIX"]
    raw_data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=False)
    close = raw_data["Close"].ffill()
    volume = raw_data["Volume"].ffill()
    return close, volume

@st.cache_data(ttl=86400)
def fetch_macro_data():
    end = datetime.now()
    start = end - timedelta(days=730)
    try:
        metrics = {"CPI": "CPIAUCSL", "Spread": "T10Y2Y", "Rate": "FEDFUNDS"}
        dfs = []
        for name, code in metrics.items():
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}&cosd={start.strftime('%Y-%m-%d')}&coed={end.strftime('%Y-%m-%d')}"
            r = requests.get(url, timeout=15)
            dfs.append(pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True).rename(columns={code: name}))
        return pd.concat(dfs, axis=1).ffill()
    except Exception: return None

# 下載資料
df_close, df_vol = fetch_system_data()
df_macro = fetch_macro_data()

# --- 3. 側邊欄：個人化參數與模式選擇 ---
st.sidebar.header("👤 1. 個人化參數")
age = st.sidebar.slider("目前年齡", 20, 75, 43)
retire = st.sidebar.slider("預計退休年齡", 50, 85, 60)
ytr = retire - age

st.sidebar.divider()
st.sidebar.header("🔘 2. 顯示模式")
view_mode = st.sidebar.radio("選擇模式", ["Beginner", "Pro", "Master"], index=2)

st.sidebar.divider()
st.sidebar.header("⚙️ 3. 景氣輸入 (PMI)")
p_col1, p_col2, p_col3 = st.sidebar.columns(3)
with p_col1: pmi_2m = st.number_input("前2月", value=52.4)
with p_col2: pmi_1m = st.number_input("上月", value=51.5)
with p_col3: pmi_curr = st.number_input("本月", value=52.2)

pmi_t = "Up" if pmi_curr > pmi_1m and pmi_curr > pmi_2m else ("Down" if pmi_curr < pmi_1m and pmi_curr < pmi_2m else "Flat")

# --- 4. 核心邏輯引擎 ---

LEVEL_ALLOCATIONS = {"Level 1": 20, "Level 2": 40, "Level 3": 60, "Level 4": 80, "Level 5": 100}

def check_ftd_master(close_s, vol_s):
    prices = close_s.dropna().tail(25)
    vols = vol_s.dropna().tail(25)
    low_idx = prices.idxmin()
    days_since_low = (prices.index[-1] - low_idx).days
    
    # FTD 偵測邏輯
    is_triggered = False
    msg = f"⏳ 觀察中 (距低點 {days_since_low}天)"
    ftd_price_level = 0.0

    if 4 <= days_since_low <= 15:
        ret = (prices.iloc[-1] / prices.iloc[-2]) - 1
        vol_up = vols.iloc[-1] > vols.iloc[-2]
        if ret >= 0.017 and vol_up:
            is_triggered = True
            ftd_price_level = prices.iloc[-1]
            msg = f"🔥 FTD 觸發 (第 {days_since_low}天)"
            
    # FTD 撤銷機制：如果價格跌破 FTD 當日低點，則信號無效
    confirmed = is_triggered and prices.iloc[-1] >= ftd_price_level * 0.98 # 容忍 2% 波動
    if is_triggered and not confirmed:
        msg = "🔴 FTD 失敗 (已破底)"
    elif confirmed:
        msg = f"✅ FTD 確認 (Level: {ftd_price_level:.1f})"
        
    return confirmed, msg

def get_drawdown_level(dd):
    if dd <= -40: return "Level 5"
    if dd <= -35: return "Level 4"
    if dd <= -30: return "Level 3"
    if dd <= -25: return "Level 2"
    if dd <= -20: return "Level 1"
    return "未觸發"

def calc_truth_alloc(age_val, ytr_val, pmi, cpi, price, ma200, dd, ftd_ok):
    # Base Glide Path
    gold, csh_base = 5.0, 5.0
    if ytr_val >= 15: b_stk, b_bnd = 80.0, 10.0
    elif ytr_val > 0: b_stk = 60.0 + (min(ytr_val, 15)/15)*20; b_bnd = 90.0 - b_stk
    else: b_stk, b_bnd = 50.0, 40.0; csh_base = 0.0
    
    # Tilt & Compression
    comp = max(0.2, (age_val - 20) / 40)
    tilt = 0.0
    if pmi > 50: tilt += 5 * comp
    if price < ma200: tilt -= 5 * comp
    if ftd_ok: tilt += 5 # FTD 特赦解除部分防守
    if dd <= -20: tilt = 10.0 # 危機解除壓縮

    stk_f = max(0.0, min(100.0, b_stk + tilt))
    bnd_f = max(0.0, 95.0 - stk_f - gold)
    
    # 現金上限 (年齡風控)
    csh_limit_ratio = 0.7 if age_val > 40 or ytr_val < 15 else 1.0
    
    return stk_f, bnd_f, gold, csh_base, b_stk, b_bnd, csh_limit_ratio

# --- 5. 數據解析與變數對接 ---
if "executed_levels" not in st.session_state: st.session_state.executed_levels = []

vt_curr = float(df_close["VT"].iloc[-1])
vt_ma200 = float(df_close["VT"].rolling(200).mean().iloc[-1])
vt_high = float(df_close["VT"].tail(252).max())
vt_dd = (vt_curr - vt_high) / vt_high * 100
vix_v = float(yf.download("^VIX", period="1d", progress=False)["Close"].iloc[-1])
cpi_v = 3.1 # 預設

ftd_confirmed, ftd_msg = check_ftd_master(df_close["VT"], df_vol["VT"])
vt_level = get_drawdown_level(vt_dd)

stk_p, bnd_p, gld_p, csh_p, b_stk_v, b_bnd_v, csh_limit_ratio = calc_truth_alloc(
    age, ytr, pmi_curr, cpi_v, vt_curr, vt_ma200, vt_dd, ftd_confirmed
)

# --- 6. 核心顯示：四張力點 Dashboard ---
st.markdown("### ⚡ 四張力點即時導航 (Market Tensions)")
tension_col1, tension_col2, tension_col3, tension_col4 = st.columns(4)

# 1️⃣ FTD 防錯張力
ftd_status = f"{'✅ Confirmed' if ftd_confirmed else '⏳ Observing'}\n\n{ftd_msg}"
tension_col1.metric("FTD 防錯 (歐奈爾)", ftd_status)

# 2️⃣ 累積再平衡張力
suggested_invest = LEVEL_ALLOCATIONS.get(vt_level, 0)
already_executed = vt_level in st.session_state.executed_levels if vt_level != "未觸發" else False
tension_col2.metric(
    "累積再平衡 (馬朱利)",
    f"Level: {vt_level}\n建議投入: {suggested_invest}%\n已執行: {'✅' if already_executed else '❌'}"
)

# 3️⃣ 債券保護張力
tension_col3.metric(
    "債券防守 (雙確認)",
    f"Base: {b_bnd_v:.1f}%\nMode調整後: {bnd_p:.1f}%",
    delta=f"{bnd_p - b_bnd_v:+.1f}%", delta_color="inverse"
)

# 4️⃣ 現金上限張力
max_deployable = csh_limit_ratio * 100
tension_col4.metric(
    "現金上限 (年齡風控)",
    f"Max 可用: {max_deployable:.0f}%\n目前目標: {csh_p:.1f}%",
    delta="風控中" if csh_limit_ratio < 1 else "火力全開"
)

# --- 7. 三層觀測模式內容 ---
st.divider()

if view_mode == "Beginner":
    st.header(f"當前模式：{'🔴 危機' if vt_dd <= -20 else ('🟡 警戒' if vt_curr < vt_ma200 else '🟢 正常')}")
    if vt_dd <= -20:
        st.error(f"👉 行動：分批抄底。今日建議投入現有的 {suggested_invest}% 現金。")
    elif vt_curr < vt_ma200:
        st.warning("👉 行動：防禦模式。停止入金股票，現金存入銀行。")
    else:
        st.success("👉 行動：持續定投。市場健康，維持複利引擎。")

elif view_mode == "Pro":
    st.subheader("📊 宏觀戰略儀表板")
    c1, c2, c3 = st.columns(3)
    c1.metric("200MA 位階", f"{vt_curr:.2f}", f"{vt_curr - vt_ma200:+.2f}")
    c2.metric("最大回檔", f"{vt_dd:.1f}%")
    c3.metric("VIX 恐慌感", f"{vix_v:.1f}")
    
    st.divider()
    st.write(f"**建議配置：** 股票 {stk_p:.1f}% / 債券 {bnd_p:.1f}% / 黃金 5% / 現金 5%")

else: # Master
    st.subheader("🧠 Master 級深度數據")
    # 此處可放 4D 儀表板、五階抄底圖表、再平衡邏輯表
    st.info("💡 Master 提示：請觀察 FTD 確認與現金上限之比例。若 FTD 確認為 True 且 VIX 下滑，則可加速 Level 部署。")
    
    # 差異化再平衡表
    st.markdown("#### 🔧 差異化再平衡建議")
    reb_data = {
        "資產": ["📈 股票", "🛡️ 債券", "⚜️ 黃金", "💵 現金"],
        "目標 %": [f"{stk_p:.1f}%", f"{bnd_p:.1f}%", "5.0%", "5.0%"],
        "再平衡閾值": ["±5%", "±5% (加壓確認)", "±2% (精確調整)", "事件觸發"],
        "觸發條件": ["偏離過大", "VIX > 20 或 破200MA", "定期對沖", f"回檔深度至 {vt_level}"]
    }
    st.table(pd.DataFrame(reb_data))

    # 血色抄底監控 (5階)
    st.markdown("#### 🩸 五階抄底雷達")
    radar_cols = st.columns(5)
    for i, lv in enumerate([-20, -25, -30, -35, -40]):
        with radar_cols[i]:
            triggered = vt_dd <= lv
            st.markdown(f"**Level {i+1} ({lv}%)**\n\n{'🔴 觸發' if triggered else '⚪ 未達'}")