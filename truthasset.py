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

FTD_INVALIDATION_WINDOW = 5
GOLD_REBALANCE_BAND = 2.0
STOCK_REBALANCE_BAND = 5.0
BOND_REBALANCE_BAND = 5.0
VIX_DELAY_LOOKBACK = 10
BOND_PROTECTION_CPI_THRESHOLD = 3.5

with st.expander("📘 新增功能判斷準則總覽", expanded=False):
    st.markdown(
        f"""
### 1. 差異化再平衡（Band-Based Rebalancing）
- 股票再平衡閾值：**±{STOCK_REBALANCE_BAND:.0f}%**
- 黃金再平衡閾值：**±{GOLD_REBALANCE_BAND:.0f}%**
- 債券再平衡閾值：**±{BOND_REBALANCE_BAND:.0f}%**
- 只有當實際權重偏離目標權重、且超出各自 band 時，才啟動再平衡。
- 再平衡建議聚焦在 **BUY / SELL / HOLD**，不再混入新入金優先邏輯。

### 2. FTD 防錯機制（FTD Guard）
- 以 [`^GSPC`](truthasset.py:28) 作為大盤觀察基準。
- 偵測到 FTD 後，會記錄 FTD 當日 Low。
- 如果 **{FTD_INVALIDATION_WINDOW} 天內** 跌破該 Low，FTD 視為失效。
- FTD 失效後，系統會：
  - 立刻從 🟢 回切到 🟡
  - 停止主動加碼
  - 顯示風險警示

### 3. 子彈保留法則（Level 5 + VIX Delay）
- 只有在 [`get_drawdown_level()`](truthasset.py:261) 判定為 **Level 5** 時啟動額外檢查。
- 觀察 [`^VIX`](truthasset.py:28) 最近 **{VIX_DELAY_LOOKBACK} 日**：
  - 線性斜率 > 0
  - 最新值同時創該區間新高
- 兩者都成立時，代表流動性危機可能未結束，延後部署現金。

### 4. 債券保護開關（Bond Protection Switch）
- 當 CPI YoY > **{BOND_PROTECTION_CPI_THRESHOLD:.1f}%** 且 [`FEDFUNDS`](truthasset.py:54) 趨勢為 Up 時啟動。
- 啟動後，原本的債券目標權重會改由現金承接。
- 目的：避免在高通膨、升息循環中承接股債雙殺風險。

### 5. 最終優先順序
1. 先判斷 FTD 是否失效
2. 再判斷市場模式（200MA / Drawdown / VIX）
3. 再判斷是否啟動債券保護
4. 再判斷 Level 5 是否因 VIX 延遲部署
5. 最後才進入差異化再平衡（BUY / SELL / HOLD）
"""
    )

# --- 2. 數據獲取引擎 (FRED + YFinance) ---
@st.cache_data(ttl=3600)
def fetch_system_data():
    end = datetime.now()
    start = end - timedelta(days=730)
    tickers = ["VT", "0050.TW", "QQQ", "^GSPC", "^VIX"]
    raw_data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=False)

    if isinstance(raw_data.columns, pd.MultiIndex):
        close = raw_data["Close"].ffill()
        volume = raw_data["Volume"].ffill()
        low = raw_data["Low"].ffill()
    else:
        close = raw_data["Close"].ffill()
        volume = raw_data["Volume"].ffill()
        low = raw_data["Low"].ffill()

    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    if isinstance(volume, pd.Series):
        volume = volume.to_frame(name=tickers[0])
    if isinstance(low, pd.Series):
        low = low.to_frame(name=tickers[0])

    return close, volume, low

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
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True)
            dfs.append(df.rename(columns={code: name}))

        macro_df = pd.concat(dfs, axis=1, sort=False)
        macro_df.columns = list(metrics.keys())

        vix_df = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=False)["Close"]
        if isinstance(vix_df, pd.DataFrame):
            vix_df = vix_df.iloc[:, 0]
        vix_df = vix_df.dropna()

        return macro_df, vix_df
    except Exception:
        return None, None

# 下載資料
df_close, df_vol, df_low = fetch_system_data()
df_macro, df_vix = fetch_macro_data()

# --- 3. 側邊欄：個人化參數與 PMI 趨勢判斷 ---
st.sidebar.header("👤 1. 個人化參數")
age = st.sidebar.slider("目前年齡", 20, 75, 43)
retire = st.sidebar.slider("預計退休年齡", 50, 85, 60)
ytr = retire - age

st.sidebar.divider()
st.sidebar.header("🔘 2. 顯示模式")
view_mode = st.sidebar.radio("選擇模式", ["Beginner", "Pro", "Master"], index=2)

st.sidebar.divider()
st.sidebar.header("⚙️ 3. 當前經濟輸入")
st.sidebar.markdown(
    """
👉 PMI 數據來源：
[點此查詢 TradingEconomics (ISM 製造業 PMI)](https://tradingeconomics.com/united-states/manufacturing-pmi)
請從圖表中讀取近 3 個月數值，填入下方，系統自動幫您判斷趨勢！
"""
)

st.sidebar.markdown("**📊 輸入近 3 個月 PMI（從圖表讀取）**")
p_col1, p_col2, p_col3 = st.sidebar.columns(3)
with p_col1:
    pmi_2m = st.number_input("前2月", value=52.4, step=0.1)
with p_col2:
    pmi_1m = st.number_input("上月", value=51.5, step=0.1)
with p_col3:
    pmi_curr = st.number_input("本月", value=52.2, step=0.1)

if pmi_curr > pmi_1m and pmi_curr > pmi_2m:
    pmi_trend = "Up"
    pmi_label = "🟢 Up (上升)"
elif pmi_curr < pmi_1m and pmi_curr < pmi_2m:
    pmi_trend = "Down"
    pmi_label = "🔴 Down (下降)"
else:
    pmi_trend = "Flat"
    pmi_label = "⚪ Flat (震盪)"

st.sidebar.info(f"PMI 自動趨勢判斷：{pmi_label}")

with st.sidebar.expander("📚 PMI 趨勢判斷原理", expanded=False):
    st.markdown(
        """
**系統自動判斷邏輯（超簡單）**

🟢 **Up (上升)**：
本月 > 上月 **且** 本月 > 前2月

🔴 **Down (下降)**：
本月 < 上月 **且** 本月 < 前2月

⚪ **Flat (震盪)**：
不符合以上條件，例如 V 型反彈只視為震盪，不視為真正上升。
"""
    )

# --- 4. 技術指標與核心函數 ---
def find_ftd_event(close_s, vol_s):
    prices = close_s.dropna().tail(25)
    vols = vol_s.dropna().tail(25)
    if len(prices) < 15 or len(vols) < 15:
        return {"is_ftd": False, "event_date": None, "event_low": None, "days_since": None, "message": "數據不足"}

    low_idx = prices.idxmin()
    days_since_low = (prices.index[-1] - low_idx).days
    if 4 <= days_since_low <= 12:
        ret = (prices.iloc[-1] / prices.iloc[-2]) - 1
        vol_up = vols.iloc[-1] > vols.iloc[-2]
        if ret >= 0.017 and vol_up:
            return {
                "is_ftd": True,
                "event_date": prices.index[-1],
                "event_low": None,
                "days_since": days_since_low,
                "message": f"🔥 偵測到 FTD (反彈第 {days_since_low} 天)！",
            }
    return {
        "is_ftd": False,
        "event_date": None,
        "event_low": None,
        "days_since": days_since_low,
        "message": f"⏳ 觀察中 (距低點 {days_since_low} 天)",
    }

def evaluate_ftd_guard(close_s, vol_s, low_s):
    prices = close_s.dropna().tail(25)
    vols = vol_s.dropna().tail(25)
    lows = low_s.dropna().tail(25)
    if len(prices) < 15 or len(vols) < 15 or len(lows) < 15:
        return {
            "ftd_triggered": False,
            "ftd_valid": False,
            "ftd_day": None,
            "ftd_low": None,
            "days_since_ftd": None,
            "stop_investing": False,
            "message": "數據不足",
        }

    low_idx = prices.idxmin()
    days_since_low = (prices.index[-1] - low_idx).days
    if not (4 <= days_since_low <= 12):
        return {
            "ftd_triggered": False,
            "ftd_valid": False,
            "ftd_day": None,
            "ftd_low": None,
            "days_since_ftd": None,
            "stop_investing": False,
            "message": f"⏳ 尚未形成 FTD 視窗 (距低點 {days_since_low} 天)",
        }

    for idx in range(4, min(len(prices), 16)):
        ret = (prices.iloc[idx] / prices.iloc[idx - 1]) - 1
        vol_up = vols.iloc[idx] > vols.iloc[idx - 1]
        if ret >= 0.017 and vol_up:
            ftd_day = prices.index[idx]
            ftd_low = float(lows.loc[ftd_day]) if ftd_day in lows.index else float(prices.iloc[idx])
            future_lows = lows.loc[lows.index > ftd_day].head(FTD_INVALIDATION_WINDOW)
            invalidated = len(future_lows) > 0 and bool((future_lows < ftd_low).any())
            days_since_ftd = int((prices.index[-1] - ftd_day).days)
            if invalidated:
                return {
                    "ftd_triggered": True,
                    "ftd_valid": False,
                    "ftd_day": ftd_day,
                    "ftd_low": ftd_low,
                    "days_since_ftd": days_since_ftd,
                    "stop_investing": True,
                    "message": f"🔴 FTD 失效：5 天內跌破 ^GSPC FTD 當日低點 {ftd_low:.2f}，回切 🟡 並停止新入金。",
                }
            return {
                "ftd_triggered": True,
                "ftd_valid": True,
                "ftd_day": ftd_day,
                "ftd_low": ftd_low,
                "days_since_ftd": days_since_ftd,
                "stop_investing": False,
                "message": f"✅ FTD 有效：^GSPC 於 {ftd_day.strftime('%Y-%m-%d')} 出現 FTD，尚未跌破低點 {ftd_low:.2f}。",
            }

    return {
        "ftd_triggered": False,
        "ftd_valid": False,
        "ftd_day": None,
        "ftd_low": None,
        "days_since_ftd": None,
        "stop_investing": False,
        "message": f"⏳ 觀察中 (距低點 {days_since_low} 天)",
    }

def check_ftd_confirmed(close_s, vol_s=None):
    if vol_s is None:
        return False
    ftd_event = find_ftd_event(close_s, vol_s)
    return bool(ftd_event.get("is_ftd", False))

LEVEL_ALLOCATIONS = {
    "Level 1": 20,
    "Level 2": 40,
    "Level 3": 60,
    "Level 4": 80,
    "Level 5": 100,
}

def get_crisis_level(drawdown):
    if drawdown <= -40:
        return 5
    if drawdown <= -35:
        return 4
    if drawdown <= -30:
        return 3
    if drawdown <= -25:
        return 2
    if drawdown <= -20:
        return 1
    return 0

def get_drawdown_level(dd):
    level = get_crisis_level(dd)
    return f"Level {level}" if level > 0 else "未觸發"

def get_master_regime(price, ma200, drawdown, vix, ftd_guard):
    if ftd_guard.get("stop_investing"):
        return "🟡 Caution（警戒）"
    if drawdown <= -20 or vix >= 30:
        return "🔴 Crisis（危機）"
    if price < ma200:
        return "🟡 Caution（警戒）"
    if ftd_guard.get("ftd_valid"):
        return "🟢 Normal（正常）"
    return "🟢 Normal（正常）"

def get_global_regime(data_dict):
    bullish_count = 0
    for _, d in data_dict.items():
        if d["price"] > d["ma200"]:
            bullish_count += 1

    if bullish_count >= 3:
        return "🟢 Normal（多市場）"
    if bullish_count == 2:
        return "🟡 Caution（多市場）"
    return "🔴 Crisis（多市場）"

def get_macro_background(pmi, pmi_t, cpi, cpi_t, rate_t):
    if pmi > 50 and cpi_t == "Down" and rate_t == "Down":
        summary = "復甦"
        detail = "明確復甦"
    elif pmi > 50 and cpi_t == "Up" and rate_t == "Up":
        summary = "過熱"
        detail = "明確過熱"
    elif pmi <= 50 and cpi_t == "Up" and rate_t == "Up":
        summary = "滯脹"
        detail = "明確滯脹"
    elif pmi <= 50 and cpi_t == "Down" and rate_t == "Down":
        summary = "衰退"
        detail = "明確衰退"
    elif pmi > 50 and (2.0 <= cpi <= 3.0 or cpi_t == "Up") and rate_t == "Up":
        summary = "成長"
        detail = "介於：復甦 ➡️ 過熱"
    elif pmi > 50 and pmi_t == "Down" and cpi_t == "Down" and rate_t != "Down":
        summary = "過熱"
        detail = "介於：過熱 ➡️ 衰退"
    elif pmi <= 50 and pmi_t == "Up" and cpi_t == "Down" and rate_t == "Down":
        summary = "復甦"
        detail = "介於：衰退 ➡️ 復甦"
    elif pmi <= 50 and cpi >= 3.0 and cpi_t == "Down" and rate_t == "Down":
        summary = "衰退"
        detail = "介於：滯脹 ➡️ 衰退"
    elif pmi > 50:
        if cpi >= 3.0:
            summary = "過熱"
            detail = "偏向：過熱"
        else:
            summary = "成長"
            detail = "偏向：復甦"
    else:
        if cpi >= 3.0:
            summary = "滯脹"
            detail = "偏向：滯脹"
        else:
            summary = "衰退"
            detail = "偏向：衰退"

    return {
        "PMI": f"{pmi:.1f} ({pmi_t})",
        "CPI": f"{cpi:.1f}% ({cpi_t})",
        "Rate": rate_t,
        "summary": summary,
        "detail": detail,
        "note": "⚠️ 黃金權重不可降低" if summary == "滯脹" else "ℹ️ 僅供背景理解，不影響買賣決策",
    }

def calc_truth_alloc(age_val, ytr_val, mode_label, bond_protection_on):
    gold = 5.0
    if ytr_val >= 15:
        b_stk = 80.0
        defense_total = 15.0
    elif ytr_val > 0:
        b_stk = 60.0 + (min(ytr_val, 15) / 15.0) * 20.0
        defense_total = 95.0 - b_stk
    else:
        b_stk = 50.0
        defense_total = 45.0

    base_cash = 5.0 if ytr_val > 0 else 0.0
    b_bnd = max(0.0, defense_total - base_cash)
    b_csh = max(0.0, defense_total - b_bnd)

    age_compression = max(0.2, (age_val - 20) / 40)
    tilt = 0.0
    tilt_reason = "Mode = Neutral"
    if "Normal" in mode_label:
        tilt = 5.0 * age_compression
        tilt_reason = f"Normal 模式給成長加碼，傾斜 = 5% × 年齡壓縮係數 {age_compression:.2f}"
    elif "Caution" in mode_label:
        tilt = -5.0 * age_compression
        tilt_reason = f"Caution 模式降低股票曝險，傾斜 = -5% × 年齡壓縮係數 {age_compression:.2f}"
    elif "Crisis" in mode_label:
        tilt = 10.0
        tilt_reason = "Crisis 模式由抄底紀律接管，股票配置改為 +10% 危機傾斜"

    stk_f = max(0.0, min(95.0, b_stk + tilt))
    defense_after = max(0.0, 95.0 - stk_f)
    csh_f = min(b_csh, defense_after)
    bnd_f = max(0.0, defense_after - csh_f)
    bond_reason = f"防守部位 = 95 - 股票 {stk_f:.1f}% = {defense_after:.1f}%；先保留現金 {csh_f:.1f}%，剩餘 {bnd_f:.1f}% 給債券"

    if bond_protection_on:
        csh_f += bnd_f
        bond_reason = f"債券保護開關啟動：原本債券 {bnd_f:.1f}% 改由現金接手，因此現金變成 {csh_f:.1f}%"
        bnd_f = 0.0

    total_alloc = stk_f + bnd_f + gold + csh_f
    alloc_gap = total_alloc - 100.0

    explain = {
        "tilt_reason": tilt_reason,
        "bond_reason": bond_reason,
        "mode_label": mode_label,
        "defense_total": defense_total,
        "defense_after": defense_after,
        "total_alloc": total_alloc,
        "alloc_gap": alloc_gap,
    }

    return stk_f, bnd_f, gold, csh_f, b_stk, b_bnd, b_csh, tilt, age_compression, explain

def evaluate_vix_ammo_delay(vix_series, level_label):
    if vix_series is None or len(vix_series) < VIX_DELAY_LOOKBACK:
        return {"delay": False, "slope": 0.0, "is_new_high": False, "message": "VIX 資料不足，無法判定子彈保留法則。"}

    recent = vix_series.dropna().tail(VIX_DELAY_LOOKBACK)
    slope = float(np.polyfit(np.arange(len(recent)), recent.values, 1)[0]) if len(recent) >= 2 else 0.0
    is_new_high = bool(recent.iloc[-1] >= recent.max())
    delay = level_label == "Level 5" and slope > 0 and is_new_high
    if delay:
        msg = f"🟠 Level 5 延遲部署：VIX 仍在創高，10 日斜率 {slope:.2f} > 0，保留子彈。"
    else:
        msg = f"🟢 VIX 子彈檢查通過：10 日斜率 {slope:.2f}，可依規則部署。"
    return {"delay": delay, "slope": slope, "is_new_high": is_new_high, "message": msg}

def evaluate_rebalance_action(asset_type, current_pct, target_pct, mkt_data):
    drift = current_pct - target_pct
    band = STOCK_REBALANCE_BAND if asset_type == "STOCK" else GOLD_REBALANCE_BAND if asset_type == "GOLD" else BOND_REBALANCE_BAND

    if asset_type == "CASH":
        if mkt_data["drawdown"] <= -20 and not mkt_data.get("vix_delay", False):
            return "TACTICAL_DEPLOYMENT"
        if mkt_data.get("vix_delay", False):
            return "DELAY_DEPLOYMENT"
        return "HOLD_CASH"

    if abs(drift) <= band:
        return "HOLD"

    if drift < 0:
        return "BUY_TO_TARGET"

    if drift > 0:
        return "SELL_TO_TARGET"

    return "HOLD"

def create_gauge(val, title, min_v, max_v, steps, suffix="%", ref=None):
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta" if ref is not None else "gauge+number",
        value=val,
        number={"suffix": suffix},
        delta={"reference": ref} if ref is not None else None,
        title={"text": title, "font": {"size": 14}},
        gauge={
            "axis": {"range": [min_v, max_v]},
            "bar": {"color": "rgba(0,0,0,0)"},
            "steps": steps,
            "threshold": {"line": {"color": "black", "width": 4}, "value": val},
        },
    ))
    fig.update_layout(height=230, margin=dict(l=10, r=10, t=70, b=10))
    return fig

def create_drawdown_gauge(ticker, label, df):
    data = df[ticker].dropna()
    curr = data.iloc[-1]
    high = data.tail(252).max()
    ma200 = data.rolling(200).mean().iloc[-1]
    dd = (curr - high) / high * 100
    ma200_rel = (ma200 - high) / high * 100

    steps = [
        {"range": [-50, -40], "color": "#7F1D1D"},
        {"range": [-40, -35], "color": "#991B1B"},
        {"range": [-35, -30], "color": "#B91C1C"},
        {"range": [-30, -25], "color": "#DC2626"},
        {"range": [-25, -20], "color": "#F97316"},
        {"range": [-20, 0], "color": "#BBF7D0"},
    ]
    dd_stage = get_drawdown_level(dd)

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=dd,
        number={"suffix": "%"},
        title={"text": f"{label}<br><span style='font-size:11px;color:gray'>血色分級: {dd_stage} | 200MA 位階: {ma200_rel:.1f}%</span>", "font": {"size": 14}},
        gauge={
            "axis": {"range": [-50, 0]},
            "steps": steps,
            "bar": {"color": "black", "thickness": 0.2},
            "threshold": {"line": {"color": "white", "width": 4}, "value": ma200_rel},
        },
    ))
    fig.update_layout(height=240, margin=dict(l=20, r=20, t=70, b=10))
    return fig

if "executed_levels" not in st.session_state:
    st.session_state.executed_levels = []

# --- 5. 數據解析 ---
is_ftd, ftd_msg = False, "觀察中"
ftd_confirmed = check_ftd_confirmed(df_close["VT"], df_vol["VT"])
ftd_guard = evaluate_ftd_guard(df_close["^GSPC"], df_vol["^GSPC"], df_low["^GSPC"])
ftd_msg = ftd_guard["message"]
is_ftd = ftd_guard["ftd_valid"]

cpi_yoy_series = None
if df_macro is not None:
    cpi_series = df_macro["CPI"].dropna()
    cpi_yoy_series = (cpi_series / cpi_series.shift(12) - 1) * 100
    cpi_yoy_series = cpi_yoy_series.dropna()

    cpi_yoy = (cpi_series.iloc[-1] / cpi_series.iloc[-13] - 1) * 100 if len(cpi_series) >= 13 else 3.0
    cpi_prev = (cpi_series.iloc[-2] / cpi_series.iloc[-14] - 1) * 100 if len(cpi_series) >= 14 else cpi_yoy
    rate_series = df_macro["Rate"].dropna()
    rate_val = float(rate_series.iloc[-1]) if len(rate_series) else 5.25
    rate_prev = float(rate_series.iloc[-2]) if len(rate_series) >= 2 else rate_val
    spread_series = df_macro["Spread"].dropna()
    spread_val = float(spread_series.iloc[-1]) if len(spread_series) else 0.0
    cpi_t = "Up" if cpi_yoy >= cpi_prev else "Down"
    rate_t = "Up" if rate_val > rate_prev else "Down" if rate_val < rate_prev else "Flat"
    vix_val = df_vix.values[-1] if df_vix is not None and len(df_vix) > 0 else 18.0
    vix_latest = float(vix_val[0] if isinstance(vix_val, (np.ndarray, list)) else vix_val)
else:
    cpi_yoy, rate_val, spread_val, cpi_t, rate_t = 3.2, 5.25, 0.5, "Up", "Up"
    vix_latest = 18.0

bond_protection_on = bool(cpi_yoy > BOND_PROTECTION_CPI_THRESHOLD and rate_t == "Up")

vt_curr_p = float(df_close["VT"].iloc[-1])
vt_ma200_v = float(df_close["VT"].rolling(200).mean().iloc[-1])
vt_high = float(df_close["VT"].tail(252).max())
vt_dd_v = ((vt_curr_p - vt_high) / vt_high) * 100
vt_level = get_drawdown_level(vt_dd_v)
vix_ammo_state = evaluate_vix_ammo_delay(df_vix, vt_level)

market_stats = {}
radar_tickers = {
    "VT": "VT (全球)",
    "^GSPC": "SPY / S&P 500",
    "QQQ": "QQQ (科技)",
    "0050.TW": "0050 (台股)",
}
radar_rows = []
for ticker, label in radar_tickers.items():
    series = df_close[ticker].dropna()
    price = float(series.iloc[-1])
    ma50 = float(series.rolling(50).mean().iloc[-1])
    ma200 = float(series.rolling(200).mean().iloc[-1])
    high_1y = float(series.tail(252).max())
    dd = (price - high_1y) / high_1y * 100
    level = get_drawdown_level(dd)
    asset_ftd = check_ftd_confirmed(series, df_vol[ticker])
    trigger = "✅ 是" if level != "未觸發" else "❌ 否"
    market_stats[ticker] = {"price": price, "ma200": ma200, "drawdown": dd, "ftd": asset_ftd}
    radar_rows.append({
        "資產": label,
        "現價": f"{price:.2f}",
        "200MA": f"{ma200:.2f}",
        "回檔 %": f"{dd:.1f}%",
        "Level": level,
        "FTD 狀態": "✅ FTD Confirmed" if asset_ftd else "⏳ 未確認",
        "是否觸發買點": trigger,
    })

single_master_regime = get_master_regime(vt_curr_p, vt_ma200_v, vt_dd_v, vix_latest, ftd_guard)
global_master_regime = get_global_regime(market_stats)

if ftd_guard.get("stop_investing"):
    decision_regime = "🟡 Caution（警戒）"
elif "Crisis" in single_master_regime or "Crisis" in global_master_regime:
    decision_regime = "🔴 Crisis（危機）"
elif "Caution" in single_master_regime or "Caution" in global_master_regime:
    decision_regime = "🟡 Caution（警戒）"
else:
    decision_regime = "🟢 Normal（正常）"

stk_p, bnd_p, gld_p, csh_p, b_stk_v, b_bnd_v, b_csh_v, tilt_used, age_compression, alloc_explain = calc_truth_alloc(age, ytr, decision_regime, bond_protection_on)
target_alloc_sum = stk_p + bnd_p + gld_p + csh_p

if "Crisis" in decision_regime:
    current_mode = "🔴 危機"
elif "Caution" in decision_regime:
    current_mode = "🟡 警戒"
else:
    current_mode = "🟢 正常"

exit_ready = vt_curr_p > vt_ma200_v and vt_dd_v > -10 and not ftd_guard.get("stop_investing")
suggested_invest = LEVEL_ALLOCATIONS.get(vt_level, 0)
if vix_ammo_state["delay"]:
    suggested_invest = 0
executed_levels = st.session_state.executed_levels
already_executed = vt_level in executed_levels if vt_level != "未觸發" else False
remaining_cash = max(0, 100 - suggested_invest)
macro_background = get_macro_background(pmi_curr, pmi_trend, cpi_yoy, cpi_t, rate_t)

# --- 6. 主決策區 ---
st.markdown("### 🧠 Layer 1：市場狀態")
with st.expander("❓ 這裡怎麼判斷？", expanded=False):
    st.markdown(
        f"""
**先看四個市場，再給最後 Mode**
- VT / SPY / QQQ / 0050.TW
- 每個市場都看：現價 vs 200MA
- 價格 > 200MA：多頭
- 價格 < 200MA：空頭 / 防守
- FTD 若在 5 天內跌破 ^GSPC 當日低點，立刻回切 🟡 並停止主動加碼
- Level 5 若 VIX 仍創高，延後部署

**現況為什麼是這樣**
- VT 現價 / 200MA：{market_stats['VT']['price']:.2f} / {market_stats['VT']['ma200']:.2f}
- SPY 現價 / 200MA：{market_stats['^GSPC']['price']:.2f} / {market_stats['^GSPC']['ma200']:.2f}
- QQQ 現價 / 200MA：{market_stats['QQQ']['price']:.2f} / {market_stats['QQQ']['ma200']:.2f}
- 0050.TW 現價 / 200MA：{market_stats['0050.TW']['price']:.2f} / {market_stats['0050.TW']['ma200']:.2f}
- VT Drawdown：{vt_dd_v:.1f}%
- VIX：{vix_latest:.1f}
- FTD Guard：{ftd_msg}
- 最終 Mode：{current_mode}
"""
    )

layer1_df = pd.DataFrame([
    {"資產": "VT", "現價": f"{market_stats['VT']['price']:.2f}", "200MA": f"{market_stats['VT']['ma200']:.2f}", "回檔": f"{market_stats['VT']['drawdown']:.1f}%", "FTD": "FTD Confirmed ✅" if market_stats['VT']['ftd'] else "未確認 ⏳", "趨勢": "多頭" if market_stats['VT']['price'] > market_stats['VT']['ma200'] else "空頭"},
    {"資產": "SPY", "現價": f"{market_stats['^GSPC']['price']:.2f}", "200MA": f"{market_stats['^GSPC']['ma200']:.2f}", "回檔": f"{market_stats['^GSPC']['drawdown']:.1f}%", "FTD": "FTD Confirmed ✅" if market_stats['^GSPC']['ftd'] else "未確認 ⏳", "趨勢": "多頭" if market_stats['^GSPC']['price'] > market_stats['^GSPC']['ma200'] else "空頭"},
    {"資產": "QQQ", "現價": f"{market_stats['QQQ']['price']:.2f}", "200MA": f"{market_stats['QQQ']['ma200']:.2f}", "回檔": f"{market_stats['QQQ']['drawdown']:.1f}%", "FTD": "FTD Confirmed ✅" if market_stats['QQQ']['ftd'] else "未確認 ⏳", "趨勢": "多頭" if market_stats['QQQ']['price'] > market_stats['QQQ']['ma200'] else "空頭"},
    {"資產": "0050.TW", "現價": f"{market_stats['0050.TW']['price']:.2f}", "200MA": f"{market_stats['0050.TW']['ma200']:.2f}", "回檔": f"{market_stats['0050.TW']['drawdown']:.1f}%", "FTD": "FTD Confirmed ✅" if market_stats['0050.TW']['ftd'] else "未確認 ⏳", "趨勢": "多頭" if market_stats['0050.TW']['price'] > market_stats['0050.TW']['ma200'] else "空頭"},
])

if view_mode == "Beginner":
    st.metric("市場模式", current_mode)
    if current_mode == "🔴 危機":
        st.error(f"👉 行動：分批抄底，今日投入 {suggested_invest}% 現金")
    elif current_mode == "🟡 警戒":
        st.warning("👉 行動：停止買股，累積現金")
    else:
        st.success("👉 行動：持續買入 VT + 0050")
    beginner_alloc = pd.DataFrame({
        "資產": ["股票", "債券", "黃金", "現金"],
        "配置": [f"{stk_p:.1f}%", f"{bnd_p:.1f}%", f"{gld_p:.1f}%", f"{csh_p:.1f}%"],
    })
    st.table(beginner_alloc)
else:
    st.table(layer1_df)
    mode_c1, mode_c2, mode_c3, mode_c4 = st.columns(4)
    mode_c1.metric("Mode", current_mode)
    mode_c2.metric("VT Drawdown", f"{vt_dd_v:.1f}%")
    mode_c3.metric("VIX", f"{vix_latest:.1f}")
    mode_c4.metric("決策 Regime", decision_regime)

    if view_mode in ["Pro", "Master"]:
        with st.expander("🩸 血色抄底監控面板 (5階回檔與 200MA 標示)", expanded=True):
            d1, d2, d3, d4 = st.columns(4)
            with d1:
                st.plotly_chart(create_drawdown_gauge("VT", "VT (全球股市)", df_close), width="stretch")
            with d2:
                st.plotly_chart(create_drawdown_gauge("0050.TW", "0050.TW (台灣50)", df_close), width="stretch")
            with d3:
                st.plotly_chart(create_drawdown_gauge("QQQ", "QQQ (納斯達克)", df_close), width="stretch")
            with d4:
                st.plotly_chart(create_drawdown_gauge("^GSPC", "S&P 500", df_close), width="stretch")

            if view_mode == "Pro":
                st.markdown("#### 🎁 抄底引擎")
                engine_c1, engine_c2, engine_c3, engine_c4 = st.columns(4)
                engine_c1.metric("當前 Level", vt_level)
                engine_c2.metric("建議投入 %", f"{suggested_invest}%")
                engine_c3.metric("剩餘現金 %", f"{remaining_cash}%")
                engine_c4.metric("FTD", "✅ Valid" if ftd_guard.get("ftd_valid") else "🟡 Guarded")

                if vt_level != "未觸發" and not already_executed and not vix_ammo_state["delay"]:
                    st.warning(f"👉 今天應投入：{suggested_invest}% 現金｜剩餘現金：{remaining_cash}%")
                elif vt_level == "Level 5" and vix_ammo_state["delay"]:
                    st.info("⏸️ Level 5 暫緩：VIX 仍創高，先保留子彈。")
                elif vt_level != "未觸發" and already_executed:
                    st.info(f"❌ 同一個 {vt_level} 已執行，避免重複買入。")

if ftd_guard.get("stop_investing"):
    st.warning(f"FTD 防錯機制啟動：{ftd_guard['message']}")
if bond_protection_on:
    st.info("債券保護開關啟動：CPI > 3.5% 且 FEDFUNDS 上行，債券目標權重以現金取代。")
if vix_ammo_state["delay"]:
    st.warning(vix_ammo_state["message"])

vt_stock_target = stk_p * (2 / 3)
tw_stock_target = stk_p * (1 / 3)
vt_stock_base = b_stk_v * (2 / 3)
tw_stock_base = b_stk_v * (1 / 3)

nav1, nav2 = st.columns([2, 1])
with nav1:
    with st.expander("❓ 這裡怎麼判斷？", expanded=False):
        st.markdown(
            f"""
**最終決策流程**
1. 判斷市場模式：Normal / Caution / Crisis
2. 檢查 FTD Guard 是否失效
3. 算年齡基礎配置
4. 套用 Mode 傾斜：+5% / -5% / +10%
5. 檢查回檔 Level 1~5
6. 檢查 Level 5 是否被 VIX 延遲
7. 再平衡採差異化閾值：股票 ±5%、黃金 ±2%、債券 ±5%
8. 再平衡依 band 直接給出 BUY / SELL / HOLD 建議

**現況為什麼是這樣**
- Mode：{current_mode}
- Exit Rule：{'啟動' if exit_ready else '未啟動'}
- FTD：{ftd_msg}
- VIX：{vix_latest:.1f}
"""
        )
    if current_mode == "🔴 危機":
        action_msg = f"分批買入｜Level={vt_level}｜今日投入 {suggested_invest}% 現金"
    elif current_mode == "🟡 警戒":
        action_msg = "停止買股｜累積現金"
    elif exit_ready:
        action_msg = "停止抄底｜回到定投｜啟動再平衡"
    else:
        action_msg = "定投 VT + 0050（3:1）"
    st.markdown(f"### 📍 當前行動指令：**{action_msg}**")

    if current_mode == "🔴 危機":
        st.error("🔴 危機模式：分批買。")
    elif current_mode == "🟡 警戒":
        st.warning("🟡 警戒模式：停止買股，累積現金。")
    else:
        st.success("🟢 正常模式：定投 VT + 0050（3:1）。")

    if exit_ready and view_mode in ["Pro", "Master"]:
        st.success("🛑 Exit Rule 啟動：價格已站上 200MA 且脫離危機區，停止抄底、回到定投並啟動再平衡。")

    if abs(stk_p - b_stk_v) < 1 and current_mode != "🔴 危機" and view_mode in ["Pro", "Master"]:
        st.info("⚠️ 提醒：目前偏離不大，避免過度交易。")

with nav2:
    st.info(f"**配置摘要**：股票 {stk_p:.1f}% / 債券 {bnd_p:.1f}% / 黃金 {gld_p:.1f}% / 現金 {csh_p:.1f}%")
    st.caption("❌ 黃金 5% 永鎖，不做賣出調整。")

if view_mode == "Master":
    st.divider()
    st.markdown("### 🎁 Layer 3：抄底引擎")
    with st.expander("❓ 這裡怎麼判斷？", expanded=False):
        st.markdown(
            f"""
**抄底 Level 規則**
- L1 = -20%
- L2 = -25%
- L3 = -30%
- L4 = -35%
- L5 = -40%

**新防錯規則**
- FTD 若 5 天內跌破 ^GSPC 當日 Low → 立刻回切 🟡 並停止主動加碼
- Level 5 若 VIX 仍創高 → 延後部署

**現況為什麼是這樣**
- VT 當前回檔：{vt_dd_v:.1f}%
- 判斷 Level：{vt_level}
- FTD Guard：{ftd_msg}
- VIX Ammo：{vix_ammo_state['message']}
"""
        )
    engine_c1, engine_c2, engine_c3, engine_c4 = st.columns(4)
    engine_c1.metric("當前 Level", vt_level)
    engine_c2.metric("建議投入 %", f"{suggested_invest}%")
    engine_c3.metric("剩餘現金 %", f"{remaining_cash}%")
    engine_c4.metric("FTD", "✅ Valid" if ftd_guard.get("ftd_valid") else "🟡 Guarded")

    if vt_level != "未觸發" and not already_executed and not vix_ammo_state["delay"]:
        st.warning(f"👉 今天應投入：{suggested_invest}% 現金｜剩餘現金：{remaining_cash}%")
    elif vt_level == "Level 5" and vix_ammo_state["delay"]:
        st.info("⏸️ Level 5 暫緩：VIX 仍創高，先保留子彈。")
    elif vt_level != "未觸發" and already_executed:
        st.info(f"❌ 同一個 {vt_level} 已執行，避免重複買入。")

if view_mode == "Master":
    st.divider()
    st.markdown("### ⚙️ Layer 2：資產配置")
    with st.expander("❓ 這裡怎麼判斷？", expanded=False):
        st.markdown(
            f"""
**原則**
- 股票 = 成長
- 債券 = 防守
- 黃金 = 主權保險，固定 5%
- 現金 = 等待期彈藥
- 若 CPI > 3.5% 且 FEDFUNDS 上行，債券防守改由現金接手

**年齡基礎配置**
- 距退休 ≥ 15 年：股票 80% / 防守 15% / 黃金 5%
- 距退休 0~15 年：股票 60~80% 線性下降
- 退休期：股票 50% / 債券 45% / 黃金 5%

**現況為什麼是這樣**
- 目前距退休：{ytr} 年
- 年齡壓縮係數：{age_compression:.2f}
- 目前模式：{current_mode}
- 套用傾斜（Tilt）：{tilt_used:+.1f}%
- Bond Protection：{'啟動' if bond_protection_on else '未啟動'}
"""
        )
    alloc_df = pd.DataFrame({
        "項目": ["VT", "0050.TW", "債券", "黃金", "現金"],
        "Base allocation": [f"{vt_stock_base:.1f}%", f"{tw_stock_base:.1f}%", f"{b_bnd_v:.1f}%", "5.0%", f"{b_csh_v:.1f}%"],
        "Mode 調整後": [f"{vt_stock_target:.1f}%", f"{tw_stock_target:.1f}%", f"{bnd_p:.1f}%", f"{gld_p:.1f}%", f"{csh_p:.1f}%"],
        "實際 vs 目標差異": [f"{vt_stock_target - vt_stock_base:+.1f}%", f"{tw_stock_target - tw_stock_base:+.1f}%", f"{bnd_p - b_bnd_v:+.1f}%", "+0.0%", f"{csh_p - b_csh_v:+.1f}%"],
    })
    st.table(alloc_df)

    if abs(target_alloc_sum - 100.0) > 0.05:
        st.error(f"⚠️ 目標配置總和異常：目前合計 {target_alloc_sum:.2f}%（偏離 100% {target_alloc_sum - 100.0:+.2f}%）")
    else:
        st.caption(f"✅ 目標配置總和檢查通過：{target_alloc_sum:.1f}%")

    explain_df = pd.DataFrame([
        {"規則": "Tilt 來源", "說明": alloc_explain["tilt_reason"]},
        {"規則": "債券 / 現金拆分", "說明": alloc_explain["bond_reason"]},
        {"規則": "最終決策模式", "說明": alloc_explain["mode_label"]},
        {"規則": "FTD 對 Tilt 的影響", "說明": "FTD 不直接改 Tilt；只透過 Guard 影響 invest gating，必要時把 Regime 壓成 Caution。"},
    ])
    st.caption(f"防守總量（Base）{alloc_explain['defense_total']:.1f}%｜Tilt 後防守總量 {alloc_explain['defense_after']:.1f}%")
    st.table(explain_df)

if view_mode in ["Pro", "Master"]:
    st.divider()
    st.header("📊 4D 宏觀儀表板觀測站")
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        steps = [{"range": [0, 2], "color": "#A7F3D0"}, {"range": [2, 3], "color": "#FDE68A"}, {"range": [3, 10], "color": "#FECACA"}]
        st.plotly_chart(create_gauge(cpi_yoy, f"CPI YoY 通膨<br><span style='font-size:11px;color:gray'>趨勢: {cpi_t}</span>", 0, 8, steps), width="stretch")
    with g2:
        steps = [{"range": [0, 2], "color": "#A7F3D0"}, {"range": [2, 4], "color": "#FDE68A"}, {"range": [4, 8], "color": "#FECACA"}]
        st.plotly_chart(create_gauge(rate_val, f"基準利率 vs 中性區間<br><span style='font-size:11px;color:gray'>趨勢: {rate_t}</span>", 0, 8, steps), width="stretch")
    with g3:
        steps = [{"range": [-3, 0], "color": "#FECACA"}, {"range": [0, 1], "color": "#FDE68A"}, {"range": [1, 4], "color": "#A7F3D0"}]
        st.plotly_chart(create_gauge(spread_val, "利差 (10Y-2Y)", -2, 4, steps, suffix=""), width="stretch")
    with g4:
        steps = [{"range": [0, 15], "color": "#BFDBFE"}, {"range": [15, 20], "color": "#A7F3D0"}, {"range": [20, 30], "color": "#FDE68A"}, {"range": [30, 60], "color": "#FECACA"}]
        st.plotly_chart(create_gauge(vix_latest, "VIX 恐慌指數", 0, 60, steps, suffix=""), width="stretch")

    with st.expander("📈 查看各指標歷史趨勢圖及原始資料列表"):
        if df_macro is not None and cpi_yoy_series is not None:
            t1, t2, t3, t4 = st.tabs(["CPI 通膨 YoY", "基準利率", "殖利率倒掛差", "VIX 恐慌指數"])
            with t1:
                st.line_chart(cpi_yoy_series.tail(12).rename("CPI YoY (%)"), height=250)
            with t2:
                st.line_chart(df_macro["Rate"].dropna().tail(12).rename("FED Funds Rate (%)"), height=250)
            with t3:
                st.line_chart(df_macro["Spread"].dropna().tail(150).rename("10Y-2Y Spread (%)"), height=250)
            with t4:
                if df_vix is not None:
                    vix_to_plot = df_vix.tail(150)
                    if isinstance(vix_to_plot, pd.DataFrame):
                        vix_to_plot = vix_to_plot.iloc[:, 0]
                    st.line_chart(vix_to_plot.rename("VIX Index"), height=250)

            st.caption("以下為近期原始不重複數據列：")
            display_df = df_macro.tail(180).dropna(subset=["CPI", "Rate"], how="all")
            st.dataframe(display_df.tail(10).sort_index(ascending=False), width="stretch")
        else:
            st.warning("目前使用手動輸入模式，無歷史數據可顯示。")


if view_mode == "Master":
    st.divider()
    st.subheader("⚖️ 再平衡行動建議 (差異化再平衡)")

    with st.expander("❓ 這裡怎麼判斷？", expanded=False):
        st.markdown(
            f"""
**最終再平衡原則**
- 股票：偏離目標 **超過 ±5%** 才動
- 黃金：偏離目標 **超過 ±2%** 才動
- 債券：偏離目標 **超過 ±5%** 才動
- 偏低於目標且超出 band：給出 BUY 建議
- 偏高於目標且超出 band：給出 SELL 建議
- Crisis 下若 Level 5 + VIX 創高，延遲部署現金

**現況為什麼是這樣**
- 目前模式：{current_mode}
- FTD Guard：{ftd_msg}
- Bond Protection：{'啟動' if bond_protection_on else '未啟動'}
- VIX Ammo：{vix_ammo_state['message']}
"""
        )

    input_c1, input_c2 = st.columns(2)
    with input_c1:
        real_vt = st.number_input("📈 VT 真實權重 (%)", min_value=0.0, max_value=100.0, value=float(round(stk_p * (2 / 3), 1)), step=0.5)
        real_bond = st.number_input("🛡️ 真實債券權重 (%)", min_value=0.0, max_value=100.0, value=float(round(bnd_p, 1)), step=0.5)
    with input_c2:
        real_0050 = st.number_input("📈 0050.TW 真實權重 (%)", min_value=0.0, max_value=100.0, value=float(round(stk_p * (1 / 3), 1)), step=0.5)
        real_gold = st.number_input("⚜️ 真實黃金權重 (%)", min_value=0.0, max_value=100.0, value=float(round(gld_p, 1)), step=0.5)
        real_cash = st.number_input("💵 真實現金權重 (%)", min_value=0.0, max_value=100.0, value=float(round(csh_p, 1)), step=0.5)

    real_stock = real_vt + real_0050
    real_alloc_sum = real_vt + real_0050 + real_bond + real_gold + real_cash
    if abs(real_alloc_sum - 100.0) > 0.1:
        st.warning(f"⚠️ 你輸入的真實持倉合計為 {real_alloc_sum:.1f}% ，不是 100%。系統不會自動正規化；以下再平衡偏離與建議僅供參考，請先把五個數字輸入欄位調整為合計 100%。")
    else:
        st.caption(f"✅ 真實持倉總和檢查通過：{real_alloc_sum:.1f}%")

    rebalance_rows = []
    asset_rebalance_map = [
        ("VT", "📈 VT", real_vt, vt_stock_target),
        ("TW", "📈 0050.TW", real_0050, tw_stock_target),
        ("BOND", "🛡️ 債券", real_bond, bnd_p),
        ("GOLD", "⚜️ 黃金", real_gold, gld_p),
        ("CASH", "💵 現金池", real_cash, csh_p),
    ]

    rebalance_status_priority = []
    market_rebalance_context = {
        "is_ftd": ftd_guard.get("ftd_valid", False),
        "price": vt_curr_p,
        "ma200": vt_ma200_v,
        "drawdown": vt_dd_v,
        "vix_delay": vix_ammo_state["delay"],
    }

    trigger_action_map = {
        "BUY_TO_TARGET": ("🟡 Buy", "買回目標配置"),
        "SELL_TO_TARGET": ("🔴 Sell", "賣回目標配置"),
        "HOLD": ("🟢 Hold", "不動"),
        "TACTICAL_DEPLOYMENT": ("🔴 Crisis Override", "危機期可戰術釋放現金"),
        "DELAY_DEPLOYMENT": ("🟠 延遲部署", "VIX 仍創高，暫緩 Level 5 現金釋放"),
        "HOLD_CASH": ("🟢 Hold", "保留現金彈藥"),
    }

    for asset_type, asset_name, real_weight, target_weight in asset_rebalance_map:
        deviation = real_weight - target_weight
        trigger = evaluate_rebalance_action(asset_type, real_weight, target_weight, market_rebalance_context)
        status, action = trigger_action_map.get(trigger, ("🟢 安全區", "不動"))
        band = STOCK_REBALANCE_BAND if asset_type in ["VT", "TW"] else GOLD_REBALANCE_BAND if asset_type == "GOLD" else BOND_REBALANCE_BAND if asset_type == "BOND" else 0.0
        reason_bits = []

        if asset_type == "CASH":
            reason_bits.append(f"現金池依 drawdown={vt_dd_v:.1f}% 與 VIX delay={vix_ammo_state['delay']} 判斷")
        else:
            reason_bits.append(f"偏離 {deviation:+.1f}% vs band ±{band:.1f}%")
            if deviation < 0:
                reason_bits.append("低於目標且超出 band，給出 BUY 建議")
            elif deviation > 0:
                reason_bits.append("高於目標且超出 band，給出 SELL 建議")
            else:
                reason_bits.append("剛好在目標附近")

        if current_mode == "🔴 危機" and asset_type in ["VT", "TW", "BOND"]:
            status = "🔴 Crisis Override"
            action = "改由抄底引擎處理"
            trigger = "TACTICAL_DEPLOYMENT"
            reason_bits.append("危機模式下，股票/債券再平衡由抄底引擎覆蓋")

        if bond_protection_on and asset_type == "BOND":
            reason_bits.append("Bond Protection 開啟，債券目標可能被現金取代")

        rebalance_status_priority.append(status)
        rebalance_rows.append({
            "資產": asset_name,
            "目標": f"{target_weight:.1f}%",
            "實際": f"{real_weight:.1f}%",
            "偏離": f"{deviation:+.1f}%",
            "觸發器": trigger,
            "再平衡狀態": status,
            "操作建議": action,
            "原因": "；".join(reason_bits),
        })

    if "🟠 延遲部署" in rebalance_status_priority:
        st.warning("🟠 子彈保留法則：Level 5 仍在流動性危機區，現金部署延後。")
    elif "🔴 Crisis Override" in rebalance_status_priority:
        st.error("🔴 Crisis：再平衡暫停，抄底引擎 override 一切。")
    elif "🔴 需要再平衡" in rebalance_status_priority:
        st.warning("🔴 需要再平衡：至少一項資產已超出差異化閾值。")
    elif "🟡 Buy" in rebalance_status_priority or "🔴 Sell" in rebalance_status_priority:
        st.info("⚖️ Band Rebalancing：至少一項資產已偏離目標配置，系統依 band 給出 BUY / SELL 建議。")
    else:
        st.success("🟢 安全區：目前沒有資產觸發 Master 再平衡。")

    st.table(pd.DataFrame(rebalance_rows))

if view_mode in ["Pro", "Master"]:
    st.divider()
    st.subheader("📡 Regime（背景資訊）")
    with st.expander("❓ 這裡怎麼判斷？", expanded=False):
        st.markdown(
            f"""
**市場模式最終判斷（決策）**
1. 趨勢：價格 vs 200MA
2. 回檔：0~-10% 正常、-10%~-20% 修正、≤-20% 危機
3. VIX：<20 正常、20~30 緊張、≥30 恐慌
4. FTD Guard 若失效，優先回到 🟡 並停止主動加碼

**{'Master 多市場確認' if view_mode == 'Master' else 'Pro 單頁背景摘要'}**
- 單一市場 Regime（VT 主軸）：{single_master_regime}
- 多市場 Regime（廣度確認）：{global_master_regime}
- 最終決策 Regime：{decision_regime}

**背景資訊（不直接覆蓋買賣）**
- PMI / CPI / Rate 只幫助理解世界，不覆蓋 200MA、Drawdown、VIX 決策
- 高通膨 + 升息時，債券保護可把債券權重改由現金承接
"""
        )
    regime_bg_df = pd.DataFrame({
        "指標": ["PMI", "CPI", "Rate", "Summary", "Detail", "FTD Guard", "Bond Protection", "VIX Ammo", "Single Regime", "Global Regime", "Decision Regime"],
        "內容": [macro_background["PMI"], macro_background["CPI"], macro_background["Rate"], macro_background["summary"], macro_background["detail"], ftd_msg, "ON" if bond_protection_on else "OFF", vix_ammo_state["message"], single_master_regime, global_master_regime, decision_regime],
    })
    st.table(regime_bg_df)
    st.caption("Regime 是讓你理解市場，不是讓你操作市場。此區塊不直接覆蓋買賣、抄底、200MA 或資產配置，但會揭示防錯狀態。")
    st.caption(macro_background["note"])
