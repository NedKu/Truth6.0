import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import io
import numpy as np
import plotly.graph_objects as go
import re
from datetime import datetime, timedelta

# --- 1. 初始化與介面設定 ---
st.set_page_config(page_title="Truth 1.0 Master Pro Dashboard", layout="wide", page_icon="🧭")
st.title("🧭 真理 1.0：大師靈魂 x 4D 宏觀導航系統 (Master Pro)")
st.markdown("遵循 **三層作戰系統**：底層被動結構、中層年齡滑動路徑 (Glide Path)、頂層 4D 宏觀微調。")

FTD_INVALIDATION_WINDOW = 5
FTD_CANCEL_PCT = 0.98
GOLD_REBALANCE_BAND = 2.0
STOCK_REBALANCE_BAND = 5.0
BOND_REBALANCE_BAND = 5.0
VIX_DELAY_LOOKBACK = 10
BOND_PROTECTION_CPI_THRESHOLD = 3.5
NEGATIVE_SURPRISE_THRESHOLD = 0.15
POSITIVE_SURPRISE_THRESHOLD = -0.15
MAX_STOCK_WEIGHT = 90.0

with st.expander("📘 新增功能判斷準則總覽", expanded=False):
    st.markdown(
        f"""
### 1. 資料來源分工
- **上方 Cleveland Fed Nowcast 指標**：使用 Cleveland Fed 官網 `Inflation, year-over-year percent change` 表格。
- **Month 判斷**：先解析表格內的月份文字，再挑出最新月份；不是直接取最底列。
- **下方 gauge / 歷史圖 / 原始資料列表**：固定使用 BLS/FRED 官方歷史資料，避免與 nowcast 混淆。

### 2. 市場模式（Decision Regime）
- 先看 FTD Guard：若 FTD 已失效，決策至少壓到 `Caution`。
- 再看 VT 主軸：
  - Drawdown ≤ -20% 或 VIX ≥ 30 → `Crisis`
  - 價格 < 200MA → `Caution`
  - 其餘 → `Normal`
- 再看四市場廣度（VT / ^GSPC / QQQ / 0050.TW）：
  - 3 個以上站上 200MA → 多市場 `Normal`
  - 2 個站上 200MA → 多市場 `Caution`
  - 其餘 → 多市場 `Crisis`
- 最終 `Decision Regime` 取兩者中 **較保守** 的結果。

### 3. FTD Guard（Follow-Through Day 防錯）
- 以 [`^GSPC`](truthasset.py:39) 為防錯基準。
- 偵測條件：低點後第 4~12 天內，日漲幅 ≥ 1.7% 且成交量高於前一日。
- 成立後記錄 FTD 收盤價，並計算取消價 = FTD 收盤價 × {FTD_CANCEL_PCT:.2f}。
- 若現價跌破取消價，FTD 改為 `🔴 Failed`，系統停止主動提高股票曝險。

### 4. 抄底引擎（Drawdown Levels）
- Level 1 = -20%
- Level 2 = -25%
- Level 3 = -30%
- Level 4 = -35%
- Level 5 = -40%
- 危機時可部署現金以上述 Level 對應比例為上限，並受股票上限與 VIX delay 約束。

### 5. VIX Delay（子彈保留）
- 只在 `Level 5` 時額外檢查。
- 若最近 **{VIX_DELAY_LOOKBACK} 日** VIX 斜率 > 0，且最新值創區間新高，則延後現金部署。

### 6. 資產配置核心
- 黃金固定 **5%**。
- 股票上限 **{MAX_STOCK_WEIGHT:.0f}%**。
- 防守資產 = 債券 + 現金。
- 基礎股債配置先由距退休年數決定，再用 Regime Tilt 微調。
- `Caution` 且 FTD 有效時，額外給股票 +5% FTD amnesty tilt。

### 7. Bond Protection（債券保護）
- 觸發條件二選一：
  - Core PCE nowcast > 3.0 且 inflation surprise > +{NEGATIVE_SURPRISE_THRESHOLD:.2f}%
  - CPI YoY > {BOND_PROTECTION_CPI_THRESHOLD:.1f}% 且 FEDFUNDS 趨勢為 Up
- 啟動後，債券目標權重改由現金承接。

### 8. 再平衡（Band-Based Rebalancing）
- 股票 band：**±{STOCK_REBALANCE_BAND:.0f}%**
- 黃金 band：**±{GOLD_REBALANCE_BAND:.0f}%**
- 債券 band：**±{BOND_REBALANCE_BAND:.0f}%**
- 低於目標且超出 band → `BUY_TO_TARGET`
- 高於目標且超出 band → `SELL_TO_TARGET`
- 現金不走固定 band，而是走事件驅動（drawdown / VIX delay / stock headroom）。
- Bond 賣出轉股還需通過：`VIX > 20` 或 `價格 < 200MA`。

### 9. 最終優先順序
1. 資料來源分流（Nowcast vs FRED）
2. FTD Guard 是否失效
3. 市場 Regime（200MA / Drawdown / VIX / 廣度）
4. 資產配置與 Tilt
5. Bond Protection
6. Level + VIX Delay
7. 再平衡 BUY / SELL / HOLD
"""
    )

# --- 2. 數據獲取引擎 (FRED + YFinance) ---
@st.cache_data(ttl=3600)
def fetch_system_data():
    end = datetime.now()
    start = end - timedelta(days=730)
    tickers = ["VT", "0050.TW", "QQQ", "^GSPC", "^VIX"]
    raw_data = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=False)

    if raw_data is None or raw_data.empty:
        empty_index = pd.DatetimeIndex([])
        empty_frame = pd.DataFrame(index=empty_index, columns=tickers, dtype=float)
        return empty_frame.copy(), empty_frame.copy(), empty_frame.copy()

    if isinstance(raw_data.columns, pd.MultiIndex):
        close = raw_data["Close"].ffill() if "Close" in raw_data.columns.get_level_values(0) else pd.DataFrame(index=raw_data.index)
        volume = raw_data["Volume"].ffill() if "Volume" in raw_data.columns.get_level_values(0) else pd.DataFrame(index=raw_data.index)
        low = raw_data["Low"].ffill() if "Low" in raw_data.columns.get_level_values(0) else pd.DataFrame(index=raw_data.index)
    else:
        close = raw_data[["Close"]].rename(columns={"Close": tickers[0]}).ffill() if "Close" in raw_data.columns else pd.DataFrame(index=raw_data.index)
        volume = raw_data[["Volume"]].rename(columns={"Volume": tickers[0]}).ffill() if "Volume" in raw_data.columns else pd.DataFrame(index=raw_data.index)
        low = raw_data[["Low"]].rename(columns={"Low": tickers[0]}).ffill() if "Low" in raw_data.columns else pd.DataFrame(index=raw_data.index)

    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    if isinstance(volume, pd.Series):
        volume = volume.to_frame(name=tickers[0])
    if isinstance(low, pd.Series):
        low = low.to_frame(name=tickers[0])

    close = close.reindex(columns=tickers)
    volume = volume.reindex(columns=tickers)
    low = low.reindex(columns=tickers)

    return close, volume, low

@st.cache_data(ttl=86400)
def fetch_macro_data():
    end = datetime.now()
    start = end - timedelta(days=730)
    try:
        metrics = {
            "CPI": "CPIAUCSL",
            "CorePCE": "PCEPILFE",
            "Spread": "T10Y2Y",
            "Rate": "FEDFUNDS",
        }
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

        pmi_url = "https://tradingeconomics.com/united-states/manufacturing-pmi"
        pmi_resp = requests.get(pmi_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        pmi_resp.raise_for_status()
        pmi_html = pmi_resp.text
        pmi_match = re.search(
            r'Manufacturing PMI in the United States increased to\s+([0-9.]+)\s+points in\s+([A-Za-z]+)\s+from\s+([0-9.]+)\s+points in\s+([A-Za-z]+)\s+of\s+([0-9]{4})',
            pmi_html,
        )

        pmi_info = None
        if pmi_match:
            current_val = float(pmi_match.group(1))
            current_month = pmi_match.group(2)
            previous_val = float(pmi_match.group(3))
            previous_month = pmi_match.group(4)
            current_year = int(pmi_match.group(5))
            pmi_info = {
                "current": current_val,
                "previous": previous_val,
                "source_url": pmi_url,
                "source_label": "TradingEconomics｜ISM 製造業 PMI",
                "current_month": current_month,
                "previous_month": previous_month,
                "current_year": current_year,
                "reference_url": "https://www.ismworld.org/",
                "reference_label": "ISM 官網（人工參考）",
            }

        nowcast_page_url = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
        nowcast_resp = requests.get(nowcast_page_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        nowcast_resp.raise_for_status()
        nowcast_html = nowcast_resp.text

        yoy_table_match = re.search(
            r"<caption>\s*Inflation, year-over-year percent change\s*</caption>.*?<tbody>(.*?)</tbody>",
            nowcast_html,
            re.IGNORECASE | re.DOTALL,
        )
        if not yoy_table_match:
            raise ValueError("Cleveland Fed YoY table not found")

        row_matches = re.findall(r"<tr>(.*?)</tr>", yoy_table_match.group(1), re.IGNORECASE | re.DOTALL)
        yoy_rows = []
        for row_html in row_matches:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.IGNORECASE | re.DOTALL)
            cleaned_cells = [re.sub(r"<.*?>", "", cell).replace("&nbsp;", " ").strip() for cell in cells]
            if len(cleaned_cells) >= 6:
                yoy_rows.append({
                    "month": cleaned_cells[0],
                    "cpi": cleaned_cells[1],
                    "core_cpi": cleaned_cells[2],
                    "pce": cleaned_cells[3],
                    "core_pce": cleaned_cells[4],
                    "updated": cleaned_cells[5],
                })

        yoy_rows = [row for row in yoy_rows if row["month"]]
        if not yoy_rows:
            raise ValueError("Cleveland Fed YoY table has no usable rows")

        def parse_pct(val):
            if val in (None, "", "—", "-"):
                return None
            return float(str(val).replace("%", "").strip())

        def parse_month_label(month_text):
            try:
                return datetime.strptime(month_text.strip(), "%B %Y")
            except Exception:
                return datetime.min

        yoy_rows = sorted(yoy_rows, key=lambda row: parse_month_label(row["month"]))
        latest_yoy_row = max(yoy_rows, key=lambda row: parse_month_label(row["month"]))
        latest_cpi_row = latest_yoy_row
        previous_rows = [row for row in yoy_rows if parse_month_label(row["month"]) < parse_month_label(latest_yoy_row["month"])]
        previous_cpi_row = max(previous_rows, key=lambda row: parse_month_label(row["month"])) if previous_rows else latest_yoy_row
        latest_core_pce_row = latest_yoy_row

        cpi_series = macro_df["CPI"].dropna()
        core_pce_series = macro_df["CorePCE"].dropna()
        cpi_actual_yoy_series = ((cpi_series / cpi_series.shift(12)) - 1).dropna() * 100
        core_pce_actual_yoy_series = ((core_pce_series / core_pce_series.shift(12)) - 1).dropna() * 100
        cpi_actual_mom = ((cpi_series / cpi_series.shift(1)) - 1) * 100 if len(cpi_series) >= 2 else pd.Series(dtype=float)
        core_pce_actual_mom = ((core_pce_series / core_pce_series.shift(1)) - 1) * 100 if len(core_pce_series) >= 2 else pd.Series(dtype=float)

        cpi_series_values = []
        cpi_series_index = []
        for row in yoy_rows:
            cpi_val = parse_pct(row["cpi"])
            if cpi_val is not None:
                cpi_series_values.append(cpi_val)
                cpi_series_index.append(row["month"])

        cpi_nowcast_info = {
            "source_url": nowcast_page_url,
            "source_label": "Cleveland Fed｜Inflation Nowcasting YoY Table",
            "updated_at": latest_yoy_row["updated"],
            "cpi_yoy_current": parse_pct(latest_cpi_row["cpi"]) if latest_cpi_row else None,
            "cpi_yoy_label": latest_cpi_row["month"] if latest_cpi_row else latest_yoy_row["month"],
            "cpi_yoy_prev": parse_pct(previous_cpi_row["cpi"]) if previous_cpi_row else (parse_pct(latest_cpi_row["cpi"]) if latest_cpi_row else None),
            "cpi_actual_yoy": float(cpi_actual_yoy_series.iloc[-1]) if not cpi_actual_yoy_series.empty else None,
            "cpi_actual_label": cpi_actual_yoy_series.index[-1].strftime("%Y-%m") if not cpi_actual_yoy_series.empty else None,
            "pce_core_yoy_nowcast": parse_pct(latest_core_pce_row["core_pce"]) if latest_core_pce_row else None,
            "pce_core_yoy_label": latest_core_pce_row["month"] if latest_core_pce_row else latest_yoy_row["month"],
            "pce_core_yoy_actual": float(core_pce_actual_yoy_series.iloc[-1]) if not core_pce_actual_yoy_series.empty else None,
            "pce_core_actual_label": core_pce_actual_yoy_series.index[-1].strftime("%Y-%m") if not core_pce_actual_yoy_series.empty else None,
            "cpi_yoy_series": pd.Series(cpi_series_values, index=cpi_series_index, name="CPI Nowcast YoY (%)") if cpi_series_values else None,
        }

        cpi_nowcast_info["cpi_mom_nowcast"] = None
        cpi_nowcast_info["pce_core_mom_nowcast"] = None
        cpi_nowcast_info["cpi_prev_actual_mom"] = float(cpi_actual_mom.dropna().iloc[-1]) if not cpi_actual_mom.dropna().empty else None
        cpi_nowcast_info["pce_prev_actual_mom"] = float(core_pce_actual_mom.dropna().iloc[-1]) if not core_pce_actual_mom.dropna().empty else None

        return macro_df, vix_df, pmi_info, cpi_nowcast_info
    except Exception:
        return None, None, None, None

# 下載資料
df_close, df_vol, df_low = fetch_system_data()
df_macro, df_vix, pmi_info, cpi_nowcast_info = fetch_macro_data()

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
    "[🔗 ISM 製造業 PMI 近月數值來源｜TradingEconomics](https://tradingeconomics.com/united-states/manufacturing-pmi)"
)
st.sidebar.markdown(
    "[🔗 ISM 官網即時參考｜ismworld.org](https://www.ismworld.org/)"
)

st.sidebar.markdown("**📊 輸入近 3 個月 PMI（維持原本 3 個月判斷邏輯）**")
if pmi_info is not None:
    pmi_curr_default = float(pmi_info["current"])
    pmi_1m_default = float(pmi_info["previous"])
    st.sidebar.success(
        f"已自動帶入近 2 個月 PMI：{pmi_info['previous_month']} {pmi_1m_default:.1f} → {pmi_info['current_month']} {pmi_curr_default:.1f}"
    )
    st.sidebar.caption(
        f"資料來源：{pmi_info['source_label']}｜更新月份：{pmi_info['current_month']} {pmi_info['current_year']}"
    )
    st.sidebar.info(
        "請只手動補上『前2月』數值；『上月』與『本月』已依公開網頁最新數值自動帶入。"
    )
    st.sidebar.caption(
        f"ISM 官網目前因驗證機制無法後端自動解析，請開啟 [{pmi_info['reference_label']}]({pmi_info['reference_url']}) 作為即時變化參考。"
    )
    st.sidebar.markdown(
        f"**PMI 即時參考值（公開頁面最新）**  \
- {pmi_info['current_month']}：`{pmi_curr_default:.1f}`  \
- {pmi_info['previous_month']}：`{pmi_1m_default:.1f}`"
    )
else:
    pmi_curr_default = 52.2
    pmi_1m_default = 51.5
    st.sidebar.warning("PMI 近 2 個月自動抓取失敗，請手動輸入 3 個月數值。")

p_col1, p_col2, p_col3 = st.sidebar.columns(3)
with p_col1:
    pmi_2m = st.number_input("前2月（手動）", value=52.4, step=0.1)
with p_col2:
    pmi_1m = st.number_input("上月（自動，可覆寫）", value=float(pmi_1m_default), step=0.1)
with p_col3:
    pmi_curr = st.number_input("本月（自動，可覆寫）", value=float(pmi_curr_default), step=0.1)

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
            "ftd_price": None,
            "cancel_price": None,
            "status": "Observing",
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
            "ftd_price": None,
            "cancel_price": None,
            "status": "Observing",
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
            ftd_price = float(prices.iloc[idx])
            cancel_price = ftd_price * FTD_CANCEL_PCT
            current_price = float(prices.iloc[-1])
            days_since_ftd = int((prices.index[-1] - ftd_day).days)
            invalidated = current_price < cancel_price
            if invalidated:
                return {
                    "ftd_triggered": True,
                    "ftd_valid": False,
                    "ftd_day": ftd_day,
                    "ftd_low": ftd_low,
                    "ftd_price": ftd_price,
                    "cancel_price": cancel_price,
                    "status": "🔴 Failed",
                    "days_since_ftd": days_since_ftd,
                    "stop_investing": True,
                    "message": f"🔴 FTD 失效：現價 {current_price:.2f} 跌破 FTD 容損價 {cancel_price:.2f}（FTD 收盤 {ftd_price:.2f} 的 2% 下方）。",
                }
            return {
                "ftd_triggered": True,
                "ftd_valid": True,
                "ftd_day": ftd_day,
                "ftd_low": ftd_low,
                "ftd_price": ftd_price,
                "cancel_price": cancel_price,
                "status": "Confirmed",
                "days_since_ftd": days_since_ftd,
                "stop_investing": False,
                "message": f"✅ FTD 有效：^GSPC 於 {ftd_day.strftime('%Y-%m-%d')} 出現 FTD，收盤 {ftd_price:.2f}，容損價 {cancel_price:.2f}。",
            }

    return {
        "ftd_triggered": False,
        "ftd_valid": False,
        "ftd_day": None,
        "ftd_low": None,
        "ftd_price": None,
        "cancel_price": None,
        "status": "Observing",
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

PHASE_EMOJI = {
    "復甦": "🟢 復甦",
    "成長": "📈 成長",
    "過熱": "🔴 過熱",
    "滯脹": "⚠️ 滯脹",
    "衰退": "📉 衰退",
}


def classify_merrill_subresult(pmi, pmi_prev, inflation_yoy, inflation_prev, rate_val, rate_prev, inflation_label):
    pmi_mom = pmi - pmi_prev
    inflation_delta = inflation_yoy - inflation_prev
    rate_t = "Up" if rate_val > rate_prev else "Down" if rate_val < rate_prev else "Flat"

    phase = "成長"
    phase_rule = "PMI > 50 且通膨大致穩定，暫列成長。"
    asset_hint = "股票(核心) / 債券(中性)"
    confidence = 3

    if pmi < 45 and pmi_mom < 0 and inflation_delta < 0 and rate_t == "Down":
        phase = "衰退"
        phase_rule = "PMI < 45 且持續走弱，通膨回落，利率下行。"
        asset_hint = "債券(防禦) / 股票(等待 FTD)"
        confidence = 5
    elif pmi < 50 and pmi_mom > 0 and inflation_yoy <= 3.0 and rate_t in ["Down", "Flat"]:
        phase = "復甦"
        phase_rule = "PMI 仍低於 50 但回升，通膨低位，利率不再偏緊。"
        asset_hint = "股票(加碼) / 債券(保護)"
        confidence = 5
    elif pmi > 55 and pmi_mom >= 0 and inflation_yoy > 3.0 and rate_t == "Up":
        phase = "過熱"
        phase_rule = "PMI > 55、通膨 > 3%、利率上行。"
        asset_hint = "黃金 / 現金"
        confidence = 5
    elif pmi < 50 and pmi_mom < 0 and inflation_yoy >= 3.0 and rate_t in ["Up", "Flat"]:
        phase = "滯脹"
        phase_rule = "PMI 跌破 50 且走弱，但通膨仍高、利率偏緊。"
        asset_hint = "黃金(避險) / 現金池"
        confidence = 5
    elif pmi > 50 and pmi_mom > 0 and 2.0 <= inflation_yoy <= 3.0:
        phase = "成長"
        phase_rule = "PMI > 50 且動能延續，通膨在 2%~3%。"
        asset_hint = "股票(核心) / 債券(中性)"
        confidence = 5
    elif pmi < 50 and inflation_yoy >= 3.0:
        phase = "滯脹"
        phase_rule = "PMI 偏弱且通膨偏高，暫列滯脹。"
        asset_hint = "黃金(避險) / 現金池"
        confidence = 3
    elif pmi < 50:
        phase = "衰退"
        phase_rule = "PMI 偏弱但訊號不足，暫列衰退。"
        asset_hint = "債券(防禦) / 股票(等待 FTD)"
        confidence = 2

    transition = f"{PHASE_EMOJI[phase]}（明確）"
    transition_rule = "訊號足夠，直接判定單一階段。"
    if confidence < 5:
        if phase == "成長":
            transition = f"{PHASE_EMOJI['復甦']} ➔ {PHASE_EMOJI['成長']}"
            transition_rule = "PMI 已站上 50，但通膨/利率條件仍不足以完全定義單一階段。"
        elif phase == "滯脹":
            transition = f"{PHASE_EMOJI['過熱']} ➔ {PHASE_EMOJI['滯脹']}"
            transition_rule = "通膨偏高但動能走弱，顯示從過熱往滯脹移動。"
        elif phase == "衰退":
            transition = f"{PHASE_EMOJI['滯脹']} ➔ {PHASE_EMOJI['衰退']}"
            transition_rule = "經濟偏弱且防守升高，但尚未滿足最嚴格衰退條件。"
        elif phase == "復甦":
            transition = f"{PHASE_EMOJI['衰退']} ➔ {PHASE_EMOJI['復甦']}"
            transition_rule = "PMI 開始改善，但仍屬恢復早期。"

    return {
        "label": inflation_label,
        "inflation": f"{inflation_yoy:.2f}% ({inflation_delta:+.2f} YoY delta)",
        "rate": f"{rate_val:.2f} ({rate_t})",
        "phase": PHASE_EMOJI[phase],
        "phase_rule": phase_rule,
        "transition": transition,
        "transition_rule": transition_rule,
        "asset_hint": asset_hint,
        "confidence": confidence,
    }


def combine_merrill_subresults(source_label, pmi, pmi_prev, cpi_yoy, cpi_prev, core_pce_yoy, core_pce_prev, rate_val, rate_prev):
    pmi_mom = pmi - pmi_prev
    cpi_result = classify_merrill_subresult(pmi, pmi_prev, cpi_yoy, cpi_prev, rate_val, rate_prev, "CPI")
    core_pce_result = classify_merrill_subresult(pmi, pmi_prev, core_pce_yoy, core_pce_prev, rate_val, rate_prev, "Core PCE")

    phase_priority = {
        PHASE_EMOJI["復甦"]: 1,
        PHASE_EMOJI["成長"]: 2,
        PHASE_EMOJI["過熱"]: 4,
        PHASE_EMOJI["滯脹"]: 5,
        PHASE_EMOJI["衰退"]: 3,
    }
    final_phase = cpi_result["phase"] if phase_priority[cpi_result["phase"]] >= phase_priority[core_pce_result["phase"]] else core_pce_result["phase"]
    final_reason = f"{source_label} 綜合 PMI、CPI、Core PCE 與利率方向後，判定為 {final_phase}。"

    return {
        "source": source_label,
        "PMI": f"{pmi:.1f} ({pmi_mom:+.1f} MoM proxy)",
        "Rate": f"{rate_val:.2f} ({'Up' if rate_val > rate_prev else 'Down' if rate_val < rate_prev else 'Flat'})",
        "cpi_result": cpi_result,
        "core_pce_result": core_pce_result,
        "phase": final_phase,
        "phase_rule": final_reason,
        "note": "⚠️ 若 CPI 與 Core PCE 訊號不完全一致，代表判斷基準仍有不足，需同步觀察兩者與後續月份。",
    }

def calc_truth_alloc(age_val, ytr_val, mode_label, bond_protection_on, ftd_confirmed=False, drawdown_val=0.0):
    gold = 5.0

    if age_val <= 35:
        base_stock, base_bond, base_cash = 90.0, 0.0, 10.0
    elif ytr_val >= 10:
        base_stock, base_bond, base_cash = 80.0, 10.0, 10.0
    elif ytr_val > 0:
        stock_floor = 50.0
        stock_ceiling = 90.0
        progress = min(max(ytr_val, 0.0), 10.0) / 10.0
        base_stock = stock_floor + (stock_ceiling - stock_floor) * progress
        defense_total = 95.0 - base_stock
        base_cash = max(5.0, defense_total * 0.25)
        base_bond = max(0.0, defense_total - base_cash)
    else:
        base_stock, base_bond, base_cash = 50.0, 40.5, 4.5

    safe_cap = min(100.0 - age_val, 50.0 + ytr_val)

    mode_state = "🟢 守成模式 (嚴格限額)"
    if drawdown_val <= -20 and ftd_confirmed:
        max_stock = 90.0
        mode_state = "🔴 掠食者模式 (上限釋放)"
    else:
        max_stock = max(35.0, safe_cap)

    age_compression = max(0.2, (age_val - 20) / 40)
    regime_tilt = 0.0
    tilt_reason = "Mode = Neutral"
    if "Normal" in mode_label:
        regime_tilt = 5.0 * age_compression
        tilt_reason = f"Normal：股票偏向 +5% × 年齡壓縮係數 = {regime_tilt:.1f}%"
    elif "Caution" in mode_label:
        regime_tilt = -5.0 * age_compression
        tilt_reason = f"Caution：股票偏向 -5% × 年齡壓縮係數 = {regime_tilt:.1f}%"
    elif "Crisis" in mode_label:
        regime_tilt = 0.0
        tilt_reason = "Crisis：Level 啟動後，股票上限打開，增額改由債券+現金分五次移動；最後仍保留總資產 10% 的債券+現金。"

    stock_raw = base_stock + regime_tilt
    overflow = max(0.0, stock_raw - max_stock)
    stk_f = min(stock_raw, max_stock)

    bond_raw = base_bond
    cash_raw = base_cash + overflow

    if bond_protection_on:
        cash_raw += bond_raw
        bond_raw = 0.0

    others_sum = bond_raw + gold + cash_raw
    remaining_space = 100.0 - stk_f
    if others_sum > 0:
        bnd_f = (bond_raw / others_sum) * remaining_space
        gld_f = (gold / others_sum) * remaining_space
        csh_f = (cash_raw / others_sum) * remaining_space
    else:
        bnd_f, gld_f, csh_f = 0.0, gold, remaining_space - gold

    total_alloc = stk_f + bnd_f + gld_f + csh_f
    alloc_gap = total_alloc - 100.0
    defense_total = base_bond + base_cash

    bond_reason = f"基礎股債現金={base_stock:.1f}/{base_bond:.1f}/{base_cash:.1f}；平時上限 {safe_cap:.1f}%；目前股票上限 {max_stock:.1f}%；溢出 {overflow:.1f}% 轉入現金；危機部署從債券+現金移轉，但總資產保留 10% 做生活緩衝"

    explain = {
        "tilt_reason": tilt_reason,
        "bond_reason": bond_reason,
        "mode_label": mode_label,
        "mode_state": mode_state,
        "safe_cap": safe_cap,
        "max_stock": max_stock,
        "defense_total": defense_total,
        "defense_after": 100.0 - stk_f - gld_f,
        "total_alloc": total_alloc,
        "alloc_gap": alloc_gap,
        "regime_tilt": regime_tilt,
        "ftd_amnesty_tilt": 5.0 if ftd_confirmed else 0.0,
        "stock_tilt": regime_tilt,
    }

    return stk_f, bnd_f, gld_f, csh_f, base_stock, base_bond, base_cash, regime_tilt, age_compression, explain

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
    band = STOCK_REBALANCE_BAND if asset_type in ["VT", "TW", "STOCK"] else GOLD_REBALANCE_BAND if asset_type == "GOLD" else BOND_REBALANCE_BAND

    if asset_type == "CASH":
        if mkt_data["drawdown"] <= -20 and not mkt_data.get("vix_delay", False):
            return "TACTICAL_DEPLOYMENT"
        if mkt_data.get("vix_delay", False):
            return "DELAY_DEPLOYMENT"
        return "HOLD_CASH"

    if abs(drift) <= band:
        return "HOLD"

    if asset_type == "BOND" and drift > 0:
        if not (mkt_data.get("vix", 0.0) > 20 or mkt_data.get("price", 0.0) < mkt_data.get("ma200", 0.0)):
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
ftd_guard = evaluate_ftd_guard(df_close["^GSPC"], df_vol["^GSPC"], df_low["^GSPC"])
ftd_confirmed = bool(ftd_guard.get("ftd_valid", False))
ftd_msg = ftd_guard["message"]
is_ftd = ftd_confirmed

cpi_yoy_series = None
cpi_actual_yoy = None
cpi_nowcast_yoy = None
core_pce_yoy = None
core_pce_actual_yoy = None
core_pce_nowcast_yoy = None
cpi_actual_label = "BLS/FRED Official CPI YoY"
cpi_nowcast_label = "Cleveland Fed Nowcast"
inflation_surprise = 0.0
inflation_surprise_label = "⚪ 中性驚喜"
inflation_surprise_detail = "Nowcast 與前月實際值差異有限"
cpi_mom_nowcast = None
cpi_prev_actual_mom = None
pce_core_mom_nowcast = None
pce_prev_actual_mom = None
if df_macro is not None:
    cpi_series = df_macro["CPI"].dropna()
    core_pce_series = df_macro["CorePCE"].dropna()
    cpi_yoy_series = ((cpi_series / cpi_series.shift(12)) - 1).dropna() * 100
    core_pce_yoy_series = ((core_pce_series / core_pce_series.shift(12)) - 1).dropna() * 100
    cpi_actual_yoy = float(cpi_yoy_series.iloc[-1]) if not cpi_yoy_series.empty else 3.0
    core_pce_actual_yoy = float(core_pce_yoy_series.iloc[-1]) if not core_pce_yoy_series.empty else 3.0
    cpi_prev = float(cpi_yoy_series.iloc[-2]) if len(cpi_yoy_series) >= 2 else cpi_actual_yoy
    core_pce_prev = float(core_pce_yoy_series.iloc[-2]) if len(core_pce_yoy_series) >= 2 else core_pce_actual_yoy

    cpi_yoy = cpi_actual_yoy
    core_pce_yoy = core_pce_actual_yoy
    if cpi_nowcast_info is not None:
        if cpi_nowcast_info.get("cpi_yoy_current") is not None:
            cpi_nowcast_yoy = float(cpi_nowcast_info["cpi_yoy_current"])
            cpi_nowcast_label = f"Cleveland Fed Nowcast CPI YoY ({cpi_nowcast_info.get('cpi_yoy_label', 'Latest')})"
        if cpi_nowcast_info.get("pce_core_yoy_nowcast") is not None:
            core_pce_nowcast_yoy = float(cpi_nowcast_info["pce_core_yoy_nowcast"])
        if cpi_nowcast_info.get("cpi_actual_yoy") is not None:
            cpi_actual_yoy = float(cpi_nowcast_info["cpi_actual_yoy"])
        cpi_mom_nowcast = cpi_nowcast_info.get("cpi_mom_nowcast")
        cpi_prev_actual_mom = cpi_nowcast_info.get("cpi_prev_actual_mom")
        pce_core_mom_nowcast = cpi_nowcast_info.get("pce_core_mom_nowcast")
        pce_prev_actual_mom = cpi_nowcast_info.get("pce_prev_actual_mom")

    if pce_core_mom_nowcast is not None and pce_prev_actual_mom is not None:
        inflation_surprise = float(pce_core_mom_nowcast - pce_prev_actual_mom)
    elif cpi_mom_nowcast is not None and cpi_prev_actual_mom is not None:
        inflation_surprise = float(cpi_mom_nowcast - cpi_prev_actual_mom)

    if inflation_surprise > NEGATIVE_SURPRISE_THRESHOLD:
        inflation_surprise_label = "🚨 負面驚喜"
        inflation_surprise_detail = "Nowcast 高於前月實際值超過 +0.15%，債券保護張力升高"
    elif inflation_surprise < POSITIVE_SURPRISE_THRESHOLD:
        inflation_surprise_label = "🟢 正面驚喜"
        inflation_surprise_detail = "Nowcast 低於前月實際值超過 -0.15%，提升 FTD 買入信心"

    rate_series = df_macro["Rate"].dropna()
    rate_val = float(rate_series.iloc[-1]) if len(rate_series) else 5.25
    rate_prev = float(rate_series.iloc[-2]) if len(rate_series) >= 2 else rate_val
    spread_series = df_macro["Spread"].dropna()
    spread_val = float(spread_series.iloc[-1]) if len(spread_series) else 0.0
    cpi_t = "Up" if cpi_yoy >= cpi_prev else "Down"
    core_pce_t = "Up" if core_pce_yoy >= core_pce_prev else "Down"
    rate_t = "Up" if rate_val > rate_prev else "Down" if rate_val < rate_prev else "Flat"
    vix_val = df_vix.values[-1] if df_vix is not None and len(df_vix) > 0 else 18.0
    vix_latest = float(vix_val[0] if isinstance(vix_val, (np.ndarray, list)) else vix_val)
else:
    cpi_yoy, cpi_actual_yoy, core_pce_yoy, core_pce_actual_yoy, rate_val, spread_val, cpi_t, core_pce_t, rate_t = 3.2, 3.2, 3.0, 3.0, 5.25, 0.5, "Up", "Up", "Up"
    vix_latest = 18.0

bond_protection_on = bool(
    (core_pce_yoy is not None and core_pce_yoy > 3.0 and inflation_surprise > NEGATIVE_SURPRISE_THRESHOLD)
    or (cpi_yoy > BOND_PROTECTION_CPI_THRESHOLD and rate_t == "Up")
)

def get_latest_series_value(frame, ticker, fallback):
    if frame is None or ticker not in frame.columns:
        return fallback
    series = frame[ticker].dropna()
    return float(series.iloc[-1]) if not series.empty else fallback


def get_latest_rolling_mean(frame, ticker, window, fallback):
    if frame is None or ticker not in frame.columns:
        return fallback
    series = frame[ticker].dropna()
    rolling_series = series.rolling(window).mean().dropna()
    return float(rolling_series.iloc[-1]) if not rolling_series.empty else fallback


def get_latest_tail_max(frame, ticker, tail_size, fallback):
    if frame is None or ticker not in frame.columns:
        return fallback
    series = frame[ticker].dropna()
    tail_series = series.tail(tail_size)
    return float(tail_series.max()) if not tail_series.empty else fallback


vt_curr_p = get_latest_series_value(df_close, "VT", 100.0)
vt_ma200_v = get_latest_rolling_mean(df_close, "VT", 200, vt_curr_p)
vt_high = get_latest_tail_max(df_close, "VT", 252, vt_curr_p)
vt_dd_v = ((vt_curr_p - vt_high) / vt_high) * 100 if vt_high else 0.0
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
    series = df_close[ticker].dropna() if df_close is not None and ticker in df_close.columns else pd.Series(dtype=float)
    vol_series = df_vol[ticker].dropna() if df_vol is not None and ticker in df_vol.columns else pd.Series(dtype=float)
    price = float(series.iloc[-1]) if not series.empty else 100.0
    ma50_series = series.rolling(50).mean().dropna() if not series.empty else pd.Series(dtype=float)
    ma200_series = series.rolling(200).mean().dropna() if not series.empty else pd.Series(dtype=float)
    ma50 = float(ma50_series.iloc[-1]) if not ma50_series.empty else price
    ma200 = float(ma200_series.iloc[-1]) if not ma200_series.empty else price
    high_1y = float(series.tail(252).max()) if not series.empty else price
    dd = (price - high_1y) / high_1y * 100 if high_1y else 0.0
    level = get_drawdown_level(dd)
    asset_ftd = check_ftd_confirmed(series, vol_series)
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

pmi_prev = pmi_1m
bls_core_pce_yoy = core_pce_actual_yoy if core_pce_actual_yoy is not None else core_pce_yoy
cleveland_cpi_yoy = cpi_nowcast_yoy if cpi_nowcast_yoy is not None else cpi_actual_yoy
cleveland_core_pce_yoy = core_pce_nowcast_yoy if core_pce_nowcast_yoy is not None else core_pce_actual_yoy

macro_background_bls = combine_merrill_subresults(
    "BLS/FRED",
    pmi_curr,
    pmi_prev,
    cpi_actual_yoy,
    cpi_prev,
    bls_core_pce_yoy,
    core_pce_prev,
    rate_val,
    rate_prev,
)
macro_background_nowcast = combine_merrill_subresults(
    "Cleveland Fed",
    pmi_curr,
    pmi_prev,
    cleveland_cpi_yoy,
    cpi_prev,
    cleveland_core_pce_yoy,
    core_pce_prev,
    rate_val,
    rate_prev,
)
stk_p, bnd_p, gld_p, csh_p, b_stk_v, b_bnd_v, b_csh_v, tilt_used, age_compression, alloc_explain = calc_truth_alloc(age, ytr, decision_regime, bond_protection_on, ftd_confirmed=ftd_confirmed, drawdown_val=vt_dd_v)
if ("滯脹" in str(macro_background_bls["phase"]) or "滯脹" in str(macro_background_nowcast["phase"])) and bnd_p >= 5.0:
    gld_p += 5.0
    bnd_p -= 5.0
    alloc_explain["bond_reason"] += "｜滯脹狀態：黃金由 5% 提高至 10%，額外 5% 由債券轉入黃金"
target_alloc_sum = stk_p + bnd_p + gld_p + csh_p

if "Crisis" in decision_regime:
    current_mode = "🔴 危機"
elif "Caution" in decision_regime:
    current_mode = "🟡 警戒"
else:
    current_mode = "🟢 正常"

exit_ready = vt_curr_p > vt_ma200_v and vt_dd_v > -10 and not ftd_guard.get("stop_investing")
defense_pool = bnd_p + csh_p
deployable_defense_pool = max(0.0, defense_pool - 10.0)
suggested_invest = deployable_defense_pool * (LEVEL_ALLOCATIONS.get(vt_level, 0) / 100.0)
if vix_ammo_state["delay"]:
    suggested_invest = 0
executed_levels = st.session_state.executed_levels
already_executed = vt_level in executed_levels if vt_level != "未觸發" else False
remaining_cash = csh_p
stock_headroom = max(0.0, MAX_STOCK_WEIGHT - stk_p)
deployed_pct = max(0.0, 100.0 - defense_pool - gld_p)
cash_lock_status = "🟢 自行判斷"

# --- 6. 主決策區 ---
if view_mode == "Master":
    tension_c1, tension_c2, tension_c3, tension_c4 = st.columns(4)
    regime_light = "🟢" if "Normal" in decision_regime else "🟡" if "Caution" in decision_regime else "🔴"
    regime_metric_label = "Normal" if "Normal" in decision_regime else "Caution" if "Caution" in decision_regime else "Crisis"
    regime_short_reason = "FTD/趨勢/廣度綜合後的最終模式"
    level_num = vt_level.replace("Level ", "") if vt_level != "未觸發" else "0"

    tension_c1.metric(
        "Decision Regime",
        f"{regime_light} {regime_metric_label}",
        current_mode,
    )
    tension_c1.caption(f"{decision_regime}｜{regime_short_reason}")

    tension_c2.metric("抄底 Level", f"Level {level_num}", f"投入 {suggested_invest:.1f}%")
    tension_c2.caption(f"防守資產池 {defense_pool:.1f}%｜保留 10% 不動用｜可部署池 {deployable_defense_pool:.1f}%")

    tension_c3.metric("BLS/FRED Clock Phase", macro_background_bls["phase"])
    tension_c3.caption(
        f"PMI={macro_background_bls['PMI']}｜CPI={cpi_actual_yoy:.2f}%({cpi_t})｜Core PCE={bls_core_pce_yoy:.2f}%({core_pce_t})｜Rate={macro_background_bls['Rate']}｜{macro_background_bls['phase_rule']}"
    )

    tension_c4.metric("Cleveland Fed Clock Phase", macro_background_nowcast["phase"])
    tension_c4.caption(
        f"PMI={macro_background_nowcast['PMI']}｜CPI={cleveland_cpi_yoy:.2f}%({cpi_t})｜Core PCE={cleveland_core_pce_yoy:.2f}%({core_pce_t})｜Rate={macro_background_nowcast['Rate']}｜{macro_background_nowcast['phase_rule']}"
    )

st.markdown("### 🧠 Layer 1：市場狀態")
with st.expander("❓ 這裡怎麼判斷？", expanded=False):
    st.markdown(
        f"""
**Layer 1 的目的：先決定市場風險模式，再決定能不能提高股票曝險。**

**判斷順序**
1. 先看 FTD Guard：若已失效，決策至少維持在 `Caution`。
2. 再看 VT 主軸：
   - Drawdown ≤ -20% 或 VIX ≥ 30 → `Crisis`
   - 價格 < 200MA → `Caution`
   - 其餘 → `Normal`
3. 再看四市場廣度（VT / ^GSPC / QQQ / 0050.TW 有幾個站上 200MA）。
4. 最終 `Decision Regime` 取較保守結果。

**你現在看到這個結果，是因為**
- VT 現價 / 200MA：{market_stats['VT']['price']:.2f} / {market_stats['VT']['ma200']:.2f}
- SPY 現價 / 200MA：{market_stats['^GSPC']['price']:.2f} / {market_stats['^GSPC']['ma200']:.2f}
- QQQ 現價 / 200MA：{market_stats['QQQ']['price']:.2f} / {market_stats['QQQ']['ma200']:.2f}
- 0050.TW 現價 / 200MA：{market_stats['0050.TW']['price']:.2f} / {market_stats['0050.TW']['ma200']:.2f}
- VT Drawdown：{vt_dd_v:.1f}%
- VIX：{vix_latest:.1f}
- FTD Guard：{ftd_msg}
- 單一主軸 Regime：{single_master_regime}
- 多市場 Regime：{global_master_regime}
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
        st.error(f"👉 行動：依回檔分級調整，當前可動用現金 {suggested_invest}%")
    elif current_mode == "🟡 警戒":
        st.warning("👉 行動：停止主動提高股票曝險，保留現金")
    else:
        st.success("👉 行動：維持目標配置，僅在偏離 band 時再平衡")
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
    regime_metric_label = "Normal" if "Normal" in decision_regime else "Caution" if "Caution" in decision_regime else "Crisis"
    mode_c4.metric("決策 Regime", regime_metric_label)
    mode_c4.caption(decision_regime)

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
                engine_c2.metric("可動用現金 %", f"{suggested_invest}%")
                engine_c3.metric("剩餘現金 %", f"{remaining_cash}%")
                engine_c4.metric("FTD", "✅ Valid" if ftd_guard.get("ftd_valid") else "🟡 Guarded")

                if vt_level != "未觸發" and not already_executed and not vix_ammo_state["delay"]:
                    st.warning(f"👉 目前可依分級動用 {suggested_invest}% 現金｜剩餘現金：{remaining_cash}%")
                elif vt_level == "Level 5" and vix_ammo_state["delay"]:
                    st.info("⏸️ Level 5 暫緩：VIX 仍創高，先保留子彈。")
                elif vt_level != "未觸發" and already_executed:
                    st.info(f"❌ 同一個 {vt_level} 已執行，避免重複動用現金。")

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
**這一區把前面所有判斷合成最後行動。**

**合成順序**
1. 市場風險模式：`Normal / Caution / Crisis`
2. FTD Guard 是否允許提高股票曝險
3. 年齡基礎配置 + Regime Tilt + FTD amnesty tilt
4. Bond Protection 是否把債券改由現金承接
5. Drawdown Level 是否允許事件驅動部署現金
6. Level 5 是否被 VIX delay 暫停
7. 最後才看再平衡 band，決定 `BUY / SELL / HOLD`

**你現在看到這個結論，是因為**
- Mode：{current_mode}
- Decision Regime：{decision_regime}
- Exit Rule：{'啟動' if exit_ready else '未啟動'}
- FTD：{ftd_msg}
- Bond Protection：{'啟動' if bond_protection_on else '未啟動'}
- VIX：{vix_latest:.1f}
- Level：{vt_level}
"""
        )
    if current_mode == "🔴 危機":
        action_msg = f"危機應對｜Level={vt_level}｜目前可動用 {suggested_invest}% 現金"
    elif current_mode == "🟡 警戒":
        action_msg = "停止主動提高股票曝險｜保留現金"
    elif exit_ready:
        action_msg = "停止危機部署｜回到目標配置｜啟動再平衡"
    else:
        action_msg = "維持目標配置｜必要時做 band 再平衡"
    st.markdown(f"### 📍 當前行動指令：**{action_msg}**")

    if current_mode == "🔴 危機":
        st.error("🔴 危機模式：依回檔與防錯規則動用現金。")
    elif current_mode == "🟡 警戒":
        st.warning("🟡 警戒模式：停止主動提高股票曝險，保留現金。")
    else:
        st.success("🟢 正常模式：維持目標配置，必要時再平衡。")

    if exit_ready and view_mode in ["Pro", "Master"]:
        st.success("🛑 Exit Rule 啟動：價格已站上 200MA 且脫離危機區，停止危機部署並回到再平衡框架。")

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
**Layer 3 是事件驅動現金部署，不是無條件抄底。**

**Level 規則**
- L1 = -20%
- L2 = -25%
- L3 = -30%
- L4 = -35%
- L5 = -40%

**約束條件**
- 只在觸發 Level 後才考慮部署現金。
- 5 次分批的母數 = 現金 + 債券的防守資產總和。
- 其中最後 **10% 永遠保留**，不納入部署母數。
- 每次建議部署額 =（防守資產池 - 10%）× 對應 Level 比例。
- 若 Level 5 且 VIX 仍創高，則延後部署。
- 若同一個 Level 已執行過，避免重複動用現金。

**你現在看到這個結果，是因為**
- VT 當前回檔：{vt_dd_v:.1f}%
- 判斷 Level：{vt_level}
- 建議可動用：{suggested_invest:.0f}%
- FTD Guard：{ftd_msg}
- VIX Ammo：{vix_ammo_state['message']}
- 已執行同級別：{'是' if already_executed else '否'}
"""
        )
    engine_c1, engine_c2, engine_c3, engine_c4 = st.columns(4)
    engine_c1.metric("當前 Level", vt_level)
    engine_c2.metric("本次部署額 %", f"{suggested_invest:.1f}%")
    engine_c3.metric("可部署防守池 %", f"{deployable_defense_pool:.1f}%")
    engine_c4.metric("FTD", "✅ Valid" if ftd_guard.get("ftd_valid") else "🟡 Guarded")

    if vt_level != "未觸發" and not already_executed and not vix_ammo_state["delay"]:
        st.warning(f"👉 目前可依分級動用 {suggested_invest:.1f}%｜防守資產池={defense_pool:.1f}%｜保留 10%｜可部署池={deployable_defense_pool:.1f}%")
    elif vt_level == "Level 5" and vix_ammo_state["delay"]:
        st.info("⏸️ Level 5 暫緩：VIX 仍創高，先保留子彈。")
    elif vt_level != "未觸發" and already_executed:
        st.info(f"❌ 同一個 {vt_level} 已執行，避免重複動用現金。")

if view_mode == "Master":
    st.divider()
    st.markdown("### ⚙️ Layer 2：資產配置")
    with st.expander("❓ 這裡怎麼判斷？", expanded=False):
        st.markdown(
            f"""
**Layer 2 的目標：依照目前 code，先決定基礎股債現金，再套用 Regime、FTD、Drawdown 與 Bond Protection。**

**固定原則（依 code 實際執行）**
- 黃金預設 **5%**；若 `mode_label` 文字中包含 `滯脹`，黃金改為 **10%**。
- 防守資產 = **債券 + 現金**。
- 平時股票上限使用 `safe_cap = min(100-age, 50+ytr)`。
- 若 `drawdown <= -20` 且 `ftd_confirmed=True`，股票上限改為 **90%**。

**基礎配置（依年齡 / 距退休年數）**
- `age <= 35`：基礎配置 = 股票 90 / 債券 0 / 現金 10。
- `age > 35` 且 `ytr >= 10`：基礎配置 = 股票 80 / 債券 10 / 現金 10。
- `age > 35` 且 `0 < ytr < 10`：
  - 股票會在 **90% → 50%** 間線性下降。
  - 防守總量 = `95 - 股票`。
  - 現金 = 防守總量的 25%，但至少 5%。
  - 其餘防守配置到債券。
- `ytr <= 0`（已退休）：基礎配置 = 股票 50 / 債券 40.5 / 現金 4.5。

**Tilt 規則（依 code 實際執行）**
- `Normal`：`regime_tilt = +5% × age_compression`
- `Caution`：`regime_tilt = -5% × age_compression`
- `Crisis`：不直接給固定正向 tilt；危機擴張主要依 drawdown + FTD 觸發的股票上限釋放與防守資產轉移。

**上限與溢位處理**
- `stock_raw = base_stock + regime_tilt`
- 若 `stock_raw > max_stock`，超出的 `overflow` 直接轉入現金。
- 之後把 **債券 / 黃金 / 現金** 依剩餘空間重新正規化，確保總和回到 100%，而股票不再被二次縮放。

**Bond Protection**
- 啟動後，原本債券目標全數轉入現金，債券變成 0%。

**你現在看到這個配置，是因為**
- 目前距退休：{ytr} 年
- 平時安全上限（safe cap）：{alloc_explain['safe_cap']:.1f}%
- 目前股票上限（max stock）：{alloc_explain['max_stock']:.1f}%
- 模式狀態：{alloc_explain['mode_state']}
- 目前模式：{current_mode}
- 套用 Tilt：{tilt_used:+.1f}%
- Bond Protection：{'啟動' if bond_protection_on else '未啟動'}
- 目標配置總和：{target_alloc_sum:.1f}%
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
    st.caption("上方 Cleveland Fed Nowcast 指標使用 Cleveland Fed 即時 YoY；下方儀表板與歷史趨勢圖固定使用 BLS/FRED 官方歷史資料，避免與 Nowcast 混淆。")
    cpi_compare_col1, cpi_compare_col2, cpi_compare_col3, cpi_compare_col4 = st.columns(4)
    with cpi_compare_col1:
        st.metric(cpi_nowcast_label, f"{cpi_nowcast_yoy:.2f}%" if cpi_nowcast_yoy is not None else "N/A")
    with cpi_compare_col2:
        st.metric(cpi_actual_label, f"{cpi_actual_yoy:.2f}%" if cpi_actual_yoy is not None else "N/A")
    with cpi_compare_col3:
        pce_label = f"Cleveland Fed Nowcast Core PCE YoY ({cpi_nowcast_info.get('pce_core_yoy_label', 'Latest')})" if cpi_nowcast_info is not None else "Cleveland Fed Nowcast Core PCE YoY"
        st.metric(pce_label, f"{core_pce_nowcast_yoy:.2f}%" if core_pce_nowcast_yoy is not None else "N/A")
    with cpi_compare_col4:
        pce_actual_label = "BLS/FRED Official Core PCE YoY"
        st.metric(pce_actual_label, f"{core_pce_actual_yoy:.2f}%" if core_pce_actual_yoy is not None else "N/A")
    if cpi_nowcast_info is not None:
        st.caption(f"Nowcast 來源：[{cpi_nowcast_info['source_label']}]({cpi_nowcast_info['source_url']})｜更新時間：{cpi_nowcast_info.get('updated_at', 'N/A')}｜下方 gauge / 歷史圖 / 原始資料列表皆使用 FRED 官方歷史序列")
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        steps = [{"range": [0, 2], "color": "#A7F3D0"}, {"range": [2, 3], "color": "#FDE68A"}, {"range": [3, 10], "color": "#FECACA"}]
        st.plotly_chart(create_gauge(cpi_actual_yoy, f"CPI YoY 通膨<br><span style='font-size:11px;color:gray'>來源: FRED｜趨勢: {cpi_t}</span>", 0, 8, steps), width="stretch")
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
            core_pce_plot_series = ((df_macro["CorePCE"].dropna() / df_macro["CorePCE"].dropna().shift(12)) - 1).dropna() * 100
            t1, t2, t3, t4, t5 = st.tabs(["CPI 通膨 YoY", "Core PCE YoY", "基準利率", "殖利率倒掛差", "VIX 恐慌指數"])
            with t1:
                st.line_chart(cpi_yoy_series.tail(12).rename("CPI YoY (%)"), height=250)
            with t2:
                st.line_chart(core_pce_plot_series.tail(12).rename("Core PCE YoY (%)"), height=250)
            with t3:
                st.line_chart(df_macro["Rate"].dropna().tail(12).rename("FED Funds Rate (%)"), height=250)
            with t4:
                st.line_chart(df_macro["Spread"].dropna().tail(150).rename("10Y-2Y Spread (%)"), height=250)
            with t5:
                if df_vix is not None:
                    vix_to_plot = df_vix.tail(150)
                    if isinstance(vix_to_plot, pd.DataFrame):
                        vix_to_plot = vix_to_plot.iloc[:, 0]
                    st.line_chart(vix_to_plot.rename("VIX Index"), height=250)

            st.caption("以下為近期原始不重複數據列：")
            display_df = df_macro.tail(180).dropna(subset=["CPI", "CorePCE", "Rate"], how="all")
            st.dataframe(display_df.tail(10).sort_index(ascending=False), width="stretch")
        else:
            st.warning("目前使用手動輸入模式，無歷史數據可顯示。")


if view_mode == "Master":
    st.divider()
    st.subheader("⚖️ 再平衡行動建議 (差異化再平衡)")

    with st.expander("❓ 這裡怎麼判斷？", expanded=False):
        st.markdown(
            f"""
**這一區回答：你的真實持倉，距離系統目標有多遠，需不需要動。**

**band 規則**
- 股票：偏離目標 **超過 ±{STOCK_REBALANCE_BAND:.0f}%** 才動
- 黃金：偏離目標 **超過 ±{GOLD_REBALANCE_BAND:.0f}%** 才動
- 債券：偏離目標 **超過 ±{BOND_REBALANCE_BAND:.0f}%** 才動
- 低於目標且超出 band → `BUY_TO_TARGET`
- 高於目標且超出 band → `SELL_TO_TARGET`

**額外限制**
- 現金不走固定 band，而是依 drawdown / VIX delay / stock headroom 事件驅動。
- 若目前是 `Crisis`，一般股票/債券再平衡會被危機現金調度覆蓋。
- 若要賣債買股，還需通過 `VIX > 20` 或 `價格 < 200MA` 的條件。

**你現在看到這個結果，是因為**
- 目前模式：{current_mode}
- FTD Guard：{ftd_msg}
- Bond Protection：{'啟動' if bond_protection_on else '未啟動'}
- VIX Ammo：{vix_ammo_state['message']}
- 防守資產池：{defense_pool:.1f}%
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
        "vix": vix_latest,
        "stock_headroom": stock_headroom,
    }

    trigger_action_map = {
        "BUY_TO_TARGET": ("🟡 Buy", "買回目標配置"),
        "SELL_TO_TARGET": ("🔴 Sell", "賣回目標配置"),
        "HOLD": ("🟢 Hold", "不動"),
        "TACTICAL_DEPLOYMENT": ("🔴 Crisis Override", "危機期可依規則動用現金"),
        "DELAY_DEPLOYMENT": ("🟠 延遲部署", "VIX 仍創高，暫緩 Level 5 現金動用"),
        "HOLD_CASH": ("🟢 Hold", "保留現金緩衝"),
    }

    for asset_type, asset_name, real_weight, target_weight in asset_rebalance_map:
        deviation = real_weight - target_weight
        trigger = evaluate_rebalance_action(asset_type, real_weight, target_weight, market_rebalance_context)
        status, action = trigger_action_map.get(trigger, ("🟢 安全區", "不動"))
        band = STOCK_REBALANCE_BAND if asset_type in ["VT", "TW"] else GOLD_REBALANCE_BAND if asset_type == "GOLD" else BOND_REBALANCE_BAND if asset_type == "BOND" else 0.0
        reason_bits = []

        if asset_type == "CASH":
            reason_bits.append(f"現金池依 drawdown={vt_dd_v:.1f}% 與 VIX delay={vix_ammo_state['delay']} 做事件驅動判斷")
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
            action = "改由危機現金調度規則處理"
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
        st.warning("🟠 子彈保留法則：Level 5 仍在流動性危機區，現金動用延後。")
    elif "🔴 Crisis Override" in rebalance_status_priority:
        st.error("🔴 Crisis：一般再平衡暫停，改由危機現金調度規則接管。")
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
**Regime（背景資訊）是用美林時鐘框架，把景氣背景拆成「判斷基準」與「結果」。**

**五大階段判斷基準與結果**
- 🟢 復甦：PMI < 50 但動能回升，通膨回落到低位，利率不再偏緊。
- 📈 成長：PMI > 50 且動能延續，通膨大致穩定在 2%~3%。
- 🔴 過熱：PMI > 55、通膨高於 3%、利率仍在升息循環。
- ⚠️ 滯脹：PMI < 50 且走弱，但通膨仍高且利率維持限制水位。
- 📉 衰退：PMI < 45 且持續走弱，通膨快速回落，利率開始下行。

**這一區怎麼用**
1. 同時看兩套結果：
   - BLS/FRED：市場熟悉的官方通膨口徑。
   - Cleveland Fed：更即時的 nowcast 通膨口徑。
2. 先看 `Clock Phase` 判斷目前落在哪個景氣階段。
3. 每個 source set 都是用各自的 CPI 與 Core PCE 一起判斷出最後的單一 Clock Phase。
4. 最後再配合 FTD / VIX / Rebalance 規則決定實際行動。

**{'Master 多市場確認' if view_mode == 'Master' else 'Pro 單頁背景摘要'}**
- 單一市場 Regime（VT 主軸）：{single_master_regime}
- 多市場 Regime（廣度確認）：{global_master_regime}
- 最終決策 Regime：{decision_regime}

**背景資訊（不直接覆蓋買賣）**
- 景氣階段幫助你理解「資產應偏向哪裡」。
- 真正買點 / 賣點 / 現金部署，仍由 Layer 1、FTD、Drawdown、VIX、Rebalance 主導。
"""
        )
    merrill_clock_reference_df = pd.DataFrame([
        {"景氣階段": "🟢 復甦", "判斷基準": "PMI < 50 但動能回升；通膨回落到低位；利率不再偏緊", "結果": "股票(加碼) / 債券(保護)"},
        {"景氣階段": "📈 成長", "判斷基準": "PMI > 50 且動能延續；通膨約 2%~3%；利率接近中性", "結果": "股票(核心) / 債券(中性)"},
        {"景氣階段": "🔴 過熱", "判斷基準": "PMI > 55；通膨 > 3%；利率仍在升息循環", "結果": "黃金 / 現金"},
        {"景氣階段": "⚠️ 滯脹", "判斷基準": "PMI < 50 且走弱；通膨高且黏；利率維持限制水位", "結果": "黃金(避險) / 現金池"},
        {"景氣階段": "📉 衰退", "判斷基準": "PMI < 45 且持續走弱；通膨快速回落；利率轉向下行", "結果": "債券(防禦) / 股票(等待 FTD)"},
    ])
    st.dataframe(merrill_clock_reference_df, width="stretch", hide_index=True)

    regime_bg_df = pd.DataFrame({
        "指標": [
            "PMI",
            "BLS/FRED CPI",
            "BLS/FRED Core PCE",
            "Cleveland Fed CPI",
            "Cleveland Fed Core PCE",
            "Rate",
            "BLS/FRED Clock Phase",
            "Cleveland Fed Clock Phase",
            "FTD Guard",
            "Bond Protection",
            "Inflation Surprise",
            "VIX Ammo",
            "Single Regime",
            "Global Regime",
            "Decision Regime",
        ],
        "內容": [
            macro_background_bls["PMI"],
            f"{cpi_actual_yoy:.2f}% ({cpi_t})",
            f"{bls_core_pce_yoy:.2f}% ({core_pce_t})",
            f"{cleveland_cpi_yoy:.2f}% ({cpi_t})",
            f"{cleveland_core_pce_yoy:.2f}% ({core_pce_t})",
            macro_background_bls["Rate"],
            macro_background_bls["phase"],
            macro_background_nowcast["phase"],
            ftd_msg,
            "ON" if bond_protection_on else "OFF",
            f"{inflation_surprise_label}｜{inflation_surprise:+.2f}%",
            vix_ammo_state["message"],
            single_master_regime,
            global_master_regime,
            decision_regime,
        ],
        "數值/文字結果與判斷原因": [
            f"{macro_background_bls['PMI']}｜PMI 是景氣動能基礎，MoM proxy 用來判斷轉折。",
            f"{cpi_actual_yoy:.2f}% ({cpi_t})｜BLS/FRED 官方 CPI 子結果。",
            f"{bls_core_pce_yoy:.2f}% ({core_pce_t})｜BLS/FRED 官方 Core PCE 子結果。",
            f"{cleveland_cpi_yoy:.2f}% ({cpi_t})｜Cleveland Fed 即時 CPI 子結果。",
            f"{cleveland_core_pce_yoy:.2f}% ({core_pce_t})｜Cleveland Fed 即時 Core PCE 子結果。",
            f"{macro_background_bls['Rate']}｜利率方向影響五燈號判斷。",
            f"{macro_background_bls['phase']}｜依據 PMI={macro_background_bls['PMI']}、CPI={cpi_actual_yoy:.2f}%({cpi_t})、Core PCE={bls_core_pce_yoy:.2f}%({core_pce_t})、Rate={macro_background_bls['Rate']} 綜合判斷｜原因：{macro_background_bls['phase_rule']}",
            f"{macro_background_nowcast['phase']}｜依據 PMI={macro_background_nowcast['PMI']}、CPI={cleveland_cpi_yoy:.2f}%({cpi_t})、Core PCE={cleveland_core_pce_yoy:.2f}%({core_pce_t})、Rate={macro_background_nowcast['Rate']} 綜合判斷｜原因：{macro_background_nowcast['phase_rule']}",
            f"{ftd_msg}｜FTD Guard 失效時會優先壓抑風險承擔。",
            f"{'ON' if bond_protection_on else 'OFF'}｜高通膨/升息/偏熱 surprise 時，債券權重可能由現金承接。",
            f"{inflation_surprise_label}｜{inflation_surprise:+.2f}%｜若不足以單一判定燈號，需觀察後續月份。",
            f"{vix_ammo_state['message']}｜決定 Level 5 是否延後部署。",
            f"{single_master_regime}｜只看 VT 主軸。",
            f"{global_master_regime}｜看四市場廣度。",
            f"{decision_regime}｜取較保守的一側。",
        ],
    })
    st.table(regime_bg_df)
    st.caption("Regime 是讓你理解市場，不是讓你操作市場。此區塊不直接覆蓋買賣、抄底、200MA 或資產配置，但會揭示防錯狀態。")
    st.caption(f"BLS/FRED 判讀：{macro_background_bls['note']}｜Cleveland Fed 判讀：{macro_background_nowcast['note']}｜若 CPI 與 Core PCE 子結果不一致，代表判斷機制仍不足，需持續觀察後續月份與利率方向。")
