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
- 先看 FTD Guard：若 FTD 已失效，或 VT 價格 < 200MA，決策壓回 `Caution`。
- 再看核心指標：
  - Drawdown ≤ -20% 或 VIX ≥ 30 → `Crisis`（進入掠食者模式）
  - 價格 < 200MA → `Caution`
  - 其餘 → `Normal`
- 最終 `Decision Regime` 依據市場廣度與主軸取較保守結果。

### 3. FTD Guard（Follow-Through Day 防錯）
- 以 `^GSPC` 為基準，以近25筆資料的本輪低點為基準，首次收盤上漲日算反彈第1個交易日，偵測第4~12個交易日漲幅至少1.7%且量大於前日的FTD；低點被跌破後重新起算。
- **FTD 特赦**：在 `Caution` 模式下，若 FTD 有效，系統會配發 +5% 的股票特赦權重，抵銷部分減碼。
- 若現價跌破取消價（FTD 收盤價 × {FTD_CANCEL_PCT:.2f}），FTD 轉為 `🔴 Failed`，停止積極買入。

### 4. 抄底引擎（Drawdown Levels）
- VT、SPY、0050.TW 各依本身回檔階梯（-20%/-25%/-30%/-35%/-40%）啟動專屬抄底 Level。
- 三個市場共用同一防守資產池，合計建議額受 10% 保留金與 90% 股票硬上限約束。
- 受 **VIX Delay**（VIX 創高且斜率 > 0 時暫緩）約束。

### 5. 資產配置核心 (Master Pro 6.0)
- **股票中樞 (Pivot)**：依距退休年數採 60% / 55% / 45% / 40% 四階梯。
- **黃金 (Gold)**：固定 **10%**，作為全天候防禦基石。
- **生存金 (Survival Fund)**：債券 + 現金合計強制保留至少 **10%**，絕不動用。
- **Tactical Tilt**：平日依 Regime 做 ±5% 偏移，受年齡壓縮係數縮放；危機時不縮放。

### 6. 再平衡與入金邏輯
- **Normal**：新入金按目標比例配置，維持系統平衡。
- **Caution**：停止提高股票曝險，新資金會留在 **「現金池」** 觀望。
- **Crisis**：主動將現金池資金按 Level 階梯轉入股市抄底。
- **Bond Protection**：高通膨環境下，債券權重自動由現金承接。

"""
    )

# --- 2. 數據獲取引擎 (FRED + YFinance) ---
@st.cache_data(ttl=3600)
def fetch_system_data():
    end = datetime.now()
    start = end - timedelta(days=730)
    tickers = ["VT", "0050.TW", "QQQ", "^GSPC", "^VIX", "SPY"]
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


def evaluate_oneil_daily_rule(close_series, volume_series, market_timezone):
    """Evaluate O'Neil's daily price-gain and higher-volume conditions."""
    if close_series is None or volume_series is None:
        return None

    data = pd.concat(
        [close_series.rename("close"), volume_series.rename("volume")],
        axis=1,
    )
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["volume"] = pd.to_numeric(data["volume"], errors="coerce")
    data = data.dropna(subset=["close", "volume"])

    if data.empty:
        return None

    index = pd.DatetimeIndex(pd.to_datetime(data.index))
    if index.tz is not None:
        session_dates = index.tz_convert(market_timezone).date
    else:
        session_dates = index.date

    today = pd.Timestamp.now(tz=market_timezone).date()
    completed_mask = [session_date < today for session_date in session_dates]
    data = data.loc[completed_mask]
    session_dates = [session_date for session_date, completed in zip(session_dates, completed_mask) if completed]

    if len(data) < 2:
        return None

    previous = data.iloc[-2]
    latest = data.iloc[-1]
    previous_volume = float(previous["volume"])
    latest_volume = float(latest["volume"])

    return {
        "date": session_dates[-1].isoformat(),
        "change_pct": (float(latest["close"]) / float(previous["close"]) - 1) * 100,
        "previous_volume": previous_volume,
        "volume": latest_volume,
        "volume_up": latest_volume > previous_volume,
    }

@st.cache_data(ttl=86400)
def fetch_macro_data():
    end = datetime.now()
    start = end - timedelta(days=730)
    
    macro_df = None
    vix_df = None
    pmi_info = None
    cpi_nowcast_info = None

    # 1. Fetch FRED Data
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
    except Exception as e:
        print(f"Error fetching FRED data: {e}")

    # 2. Fetch VIX Data
    try:
        vix_df = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=False)["Close"]
        if isinstance(vix_df, pd.DataFrame):
            vix_df = vix_df.iloc[:, 0]
        vix_df = vix_df.dropna()
    except Exception as e:
        print(f"Error fetching VIX data: {e}")

    # 3. Fetch PMI Data
    try:
        pmi_url = "https://tradingeconomics.com/united-states/manufacturing-pmi"
        pmi_resp = requests.get(pmi_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        pmi_resp.raise_for_status()
        pmi_html = pmi_resp.text
        pmi_match = re.search(
            r'Manufacturing PMI in the United States increased to\s+([0-9.]+)\s+points in\s+([A-Za-z]+)\s+from\s+([0-9.]+)\s+points in\s+([A-Za-z]+)\s+of\s+([0-9]{4})',
            pmi_html,
        )

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
    except Exception as e:
        print(f"Error fetching PMI data: {e}")

    # 4. Fetch Cleveland Fed Nowcast
    try:
        nowcast_page_url = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
        nowcast_resp = requests.get(nowcast_page_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        nowcast_resp.raise_for_status()
        nowcast_html = nowcast_resp.text

        # 更加彈性的 Regex，處理可能的大小寫或屬性
        yoy_table_match = re.search(
            r"<caption[^>]*>.*?Inflation, year-over-year percent change.*?</caption>.*?<tbody>(.*?)</tbody>",
            nowcast_html,
            re.IGNORECASE | re.DOTALL,
        )
        if yoy_table_match:
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

            def parse_pct(val):
                if val in (None, "", "—", "-", " "):
                    return None
                try:
                    return float(str(val).replace("%", "").strip())
                except ValueError:
                    return None

            def parse_month_label(month_text):
                try:
                    return datetime.strptime(month_text.strip(), "%B %Y")
                except Exception:
                    return datetime.min

            # 過濾掉完全沒有數據的行（例如未來月份預留位）
            valid_rows = [row for row in yoy_rows if parse_pct(row["cpi"]) is not None or parse_pct(row["core_pce"]) is not None]
            
            if valid_rows:
                valid_rows = sorted(valid_rows, key=lambda row: parse_month_label(row["month"]))
                latest_row = valid_rows[-1]
                
                # 尋找有核心數據的最新行
                latest_cpi_row = next((r for r in reversed(valid_rows) if parse_pct(r["cpi"]) is not None), latest_row)
                latest_core_pce_row = next((r for r in reversed(valid_rows) if parse_pct(r["core_pce"]) is not None), latest_row)
                
                # 尋找前一個月份
                previous_rows = [r for r in valid_rows if parse_month_label(r["month"]) < parse_month_label(latest_row["month"])]
                prev_row = previous_rows[-1] if previous_rows else latest_row

                # 計算 FRED 實際值的同比 (YoY)
                cpi_actual_yoy = None
                pce_core_actual_yoy = None
                cpi_actual_label = None
                pce_core_actual_label = None
                cpi_prev_actual_mom = None
                pce_prev_actual_mom = None

                if macro_df is not None:
                    cpi_series = macro_df["CPI"].dropna()
                    core_pce_series = macro_df["CorePCE"].dropna()
                    
                    if len(cpi_series) >= 13:
                        cpi_actual_yoy_series = ((cpi_series / cpi_series.shift(12)) - 1) * 100
                        cpi_actual_yoy = float(cpi_actual_yoy_series.iloc[-1])
                        cpi_actual_label = cpi_actual_yoy_series.index[-1].strftime("%Y-%m")
                        cpi_mom = ((cpi_series / cpi_series.shift(1)) - 1) * 100
                        cpi_prev_actual_mom = float(cpi_mom.iloc[-1])
                        
                    if len(core_pce_series) >= 13:
                        core_pce_yoy_series = ((core_pce_series / core_pce_series.shift(12)) - 1) * 100
                        pce_core_actual_yoy = float(core_pce_yoy_series.iloc[-1])
                        pce_core_actual_label = core_pce_yoy_series.index[-1].strftime("%Y-%m")
                        pce_mom = ((core_pce_series / core_pce_series.shift(1)) - 1) * 100
                        pce_prev_actual_mom = float(pce_mom.iloc[-1])

                cpi_nowcast_info = {
                    "source_url": nowcast_page_url,
                    "source_label": "Cleveland Fed｜Inflation Nowcasting",
                    "updated_at": latest_row["updated"],
                    "cpi_yoy_current": parse_pct(latest_cpi_row["cpi"]),
                    "cpi_yoy_label": latest_cpi_row["month"],
                    "cpi_yoy_prev": parse_pct(prev_row["cpi"]),
                    "cpi_actual_yoy": cpi_actual_yoy,
                    "cpi_actual_label": cpi_actual_label,
                    "pce_core_yoy_nowcast": parse_pct(latest_core_pce_row["core_pce"]),
                    "pce_core_yoy_label": latest_core_pce_row["month"],
                    "pce_core_yoy_actual": pce_core_actual_yoy,
                    "pce_core_actual_label": pce_core_actual_label,
                    "cpi_mom_nowcast": None,  # 從 YoY 表格難以直接提取 MoM
                    "pce_core_mom_nowcast": None,
                    "cpi_prev_actual_mom": cpi_prev_actual_mom,
                    "pce_prev_actual_mom": pce_prev_actual_mom,
                }
    except Exception as e:
        print(f"Error fetching Cleveland Fed data: {e}")

    return macro_df, vix_df, pmi_info, cpi_nowcast_info


@st.cache_data(ttl=3600)
def fetch_cnn_fear_greed():
    """抓取 CNN Fear & Greed Index，透過 CNN dataviz API"""
    try:
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()

        fg = data.get("fear_and_greed", {})
        hist = data.get("fear_and_greed_historical", {})
        score = float(fg.get("score", 50))
        rating = fg.get("rating", "Neutral")
        timestamp = fg.get("timestamp", "")

        prev_close = float(hist.get("previousClose", score)) if hist.get("previousClose") else score
        one_week = float(hist.get("oneWeekAgo", score)) if hist.get("oneWeekAgo") else None
        one_month = float(hist.get("oneMonthAgo", score)) if hist.get("oneMonthAgo") else None
        one_year = float(hist.get("oneYearAgo", score)) if hist.get("oneYearAgo") else None

        return {
            "score": score,
            "rating": rating,
            "timestamp": timestamp,
            "previous_close": prev_close,
            "one_week_ago": one_week,
            "one_month_ago": one_month,
            "one_year_ago": one_year,
            "source_url": "https://edition.cnn.com/markets/fear-and-greed",
            "fetch_success": True,
        }
    except Exception as e:
        print(f"Error fetching CNN Fear & Greed: {e}")
        return None


def classify_fear_greed(score):
    """
    直覺分級對照：
    0-24:  深綠 → Extreme Fear → 逢低佈局機會
    25-44: 綠黃 → Fear → 觀望偏多
    45-54: 黃燈 → Neutral → 觀望為主
    55-74: 橙燈 → Greed → 減碼觀望
    75-100: 深紅 → Extreme Greed → 減碼或保守
    """
    if score <= 24:
        return {"label": "Extreme Fear", "emoji": "🟢", "color": "#166534",
                "action": "逢低佈局機會", "action_short": "買點"}
    elif score <= 44:
        return {"label": "Fear", "emoji": "🟡", "color": "#65A30D",
                "action": "觀望偏多", "action_short": "觀望"}
    elif score <= 54:
        return {"label": "Neutral", "emoji": "🟡", "color": "#CA8A04",
                "action": "觀望為主", "action_short": "觀望"}
    elif score <= 74:
        return {"label": "Greed", "emoji": "🟠", "color": "#EA580C",
                "action": "減碼觀望", "action_short": "減碼"}
    else:
        return {"label": "Extreme Greed", "emoji": "🔴", "color": "#991B1B",
                "action": "減碼或保守", "action_short": "減碼"}


def evaluate_sentiment_cross(fg_score, vix_val, cpi_yoy, rate_val, spread_val):
    """
    CNN 情緒 vs 現有四大指標交叉驗證，產生情緒確認訊息。
    作為「頂層情緒確認層」，不直接覆蓋買賣決策。
    """
    cross_results = []

    # --- CNN vs VIX ---
    if fg_score <= 24 and vix_val >= 30:
        cross_results.append({"pair": "CNN vs VIX", "signal": "📈 強烈買入訊號",
            "detail": "Extreme Fear + VIX 高：情緒恐慌，逢低佈局核心持股機會"})
    elif fg_score >= 75 and vix_val < 20:
        cross_results.append({"pair": "CNN vs VIX", "signal": "📉 強烈防守訊號",
            "detail": "Extreme Greed + VIX 低：風險加劇，建議提高防守資產、降低股票比重"})
    elif fg_score <= 44 and vix_val >= 25:
        cross_results.append({"pair": "CNN vs VIX", "signal": "⚠️ 偏防守",
            "detail": "Fear + VIX 偏高：市場緊張，注意防禦但留意反彈訊號"})
    elif fg_score >= 55 and vix_val < 15:
        cross_results.append({"pair": "CNN vs VIX", "signal": "🟠 注意回調",
            "detail": "Greed + VIX 極低：市場過於自滿，注意回調風險"})
    else:
        cross_results.append({"pair": "CNN vs VIX", "signal": "ℹ️ 中性",
            "detail": f"CNN {fg_score:.0f} vs VIX {vix_val:.1f}：無極端衝突"})

    # --- CNN vs CPI ---
    if fg_score >= 75 and cpi_yoy > 3.5:
        cross_results.append({"pair": "CNN vs CPI", "signal": "🔴 泡沫警示",
            "detail": "Extreme Greed + 高通膨：泡沫風險，建議保守配置"})
    elif fg_score <= 24 and cpi_yoy < 2.5:
        cross_results.append({"pair": "CNN vs CPI", "signal": "🟢 逢低進場",
            "detail": "Extreme Fear + 低通膨：基本面支撐下的恐慌超賣，進場機會"})
    else:
        cross_results.append({"pair": "CNN vs CPI", "signal": "ℹ️ 中性",
            "detail": f"CNN {fg_score:.0f} vs CPI {cpi_yoy:.2f}%：無極端衝突"})

    # --- CNN vs 利率 ---
    if fg_score >= 75 and rate_val > 5.0:
        cross_results.append({"pair": "CNN vs 利率", "signal": "🔴 風險偏高",
            "detail": "Extreme Greed + 高利率：資金成本高且情緒過熱，風險偏高"})
    elif fg_score <= 24 and rate_val < 3.0:
        cross_results.append({"pair": "CNN vs 利率", "signal": "🟢 逢低機會",
            "detail": "Extreme Fear + 低利率：寬鬆環境下的恐慌，逢低機會"})
    else:
        cross_results.append({"pair": "CNN vs 利率", "signal": "ℹ️ 中性",
            "detail": f"CNN {fg_score:.0f} vs Rate {rate_val:.2f}%：無極端衝突"})

    # --- CNN vs Spread ---
    if fg_score >= 75 and spread_val < 0:
        cross_results.append({"pair": "CNN vs Spread", "signal": "🔴 極度警戒",
            "detail": "Extreme Greed + 殖利率倒掛：衰退風險與貪婪並存，極度警戒"})
    elif fg_score <= 24 and spread_val > 1.0:
        cross_results.append({"pair": "CNN vs Spread", "signal": "🟢 超賣機會",
            "detail": "Extreme Fear + 正常利差：經濟結構正常下的恐慌超賣"})
    else:
        cross_results.append({"pair": "CNN vs Spread", "signal": "ℹ️ 中性",
            "detail": f"CNN {fg_score:.0f} vs Spread {spread_val:.2f}：無極端衝突"})

    # 綜合訊息
    strong_buy = sum(1 for r in cross_results if "🟢" in r["signal"])
    strong_sell = sum(1 for r in cross_results if "🔴" in r["signal"] or "📉" in r["signal"])
    if strong_buy >= 2:
        summary = "📈 多重指標確認：恐慌超賣，逢低佈局核心持股機會"
    elif strong_sell >= 2:
        summary = "📉 多重指標確認：風險加劇，建議提高防守資產、降低股票比重"
    elif strong_buy == 1:
        summary = "⚠️ 部分指標顯示買入訊號，但仍需觀察其他確認"
    elif strong_sell == 1:
        summary = "⚠️ 部分指標顯示防守訊號，但仍需觀察其他確認"
    else:
        summary = "ℹ️ CNN 情緒與現有指標無極端衝突，維持現有判斷"

    return cross_results, summary


# 下載資料
df_close, df_vol, df_low = fetch_system_data()
df_macro, df_vix, pmi_info, cpi_nowcast_info = fetch_macro_data()
cnn_fg_data = fetch_cnn_fear_greed()

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

st.sidebar.divider()
st.sidebar.header("📌 4. CNN Fear & Greed")
st.sidebar.markdown(
    "[🔗 CNN Fear & Greed Index](https://edition.cnn.com/markets/fear-and-greed)"
)
if cnn_fg_data is not None and cnn_fg_data.get("fetch_success"):
    fg_score_default = float(cnn_fg_data["score"])
    fg_class = classify_fear_greed(fg_score_default)
    st.sidebar.success(
        f"已自動抓取 CNN Fear & Greed：{fg_class['emoji']} {fg_score_default:.0f} ({fg_class['label']})"
    )
    st.sidebar.caption(
        f"資料來源：[CNN Fear & Greed Index]({cnn_fg_data['source_url']})"
    )
else:
    fg_score_default = 50.0
    st.sidebar.warning("CNN Fear & Greed 自動抓取失敗，請手動輸入數值。")

fg_score_input = st.sidebar.number_input(
    "CNN F&G 分數（0-100，可覆寫）", min_value=0.0, max_value=100.0,
    value=fg_score_default, step=1.0
)
fg_classification = classify_fear_greed(fg_score_input)
st.sidebar.info(
    f"CNN 情緒判斷：{fg_classification['emoji']} {fg_classification['label']}｜建議：{fg_classification['action']}"
)

# --- 4. 技術指標與核心函數 ---
def _find_rally_ftd(close_s, vol_s, low_s=None):
    columns = [close_s.rename("close"), vol_s.rename("volume")]
    if low_s is not None:
        columns.append(low_s.rename("low"))
    data = pd.concat(columns, axis=1).sort_index()
    data = data.apply(pd.to_numeric, errors="coerce").dropna()
    data = data.loc[~data.index.duplicated(keep="last")].tail(25)
    result = {"event": None, "rally_day": None, "rally_start": None}
    if len(data) < 15:
        result["message"] = "數據不足"
        return result
    low_column = "low" if low_s is not None else "close"
    bottom = int(np.argmin(data[low_column].to_numpy()))
    start = next(
        (i for i in range(max(1, bottom), len(data))
         if data["close"].iloc[i] > data["close"].iloc[i - 1]),
        None,
    )
    if start is None:
        result["message"] = "⏳ 尚未開始反彈嘗試"
        return result
    result["rally_start"] = data.index[start]
    result["rally_day"] = len(data) - start
    for i in range(start + 3, min(start + 12, len(data))):
        previous_close = float(data["close"].iloc[i - 1])
        if previous_close <= 0:
            continue
        change = float(data["close"].iloc[i]) / previous_close - 1
        previous_volume = float(data["volume"].iloc[i - 1])
        volume = float(data["volume"].iloc[i])
        if change >= 0.017 and volume > previous_volume:
            result["event"] = {
                "date": data.index[i],
                "low": float(data[low_column].iloc[i]),
                "price": float(data["close"].iloc[i]),
                "previous_close": previous_close,
                "change_pct": change * 100,
                "previous_volume": previous_volume,
                "volume": volume,
                "rally_day": i - start + 1,
                "rally_start": data.index[start],
                "sessions_since": len(data) - i - 1,
            }
            break
    result["message"] = f"⏳ 觀察中 (反彈第 {result['rally_day']} 個交易日)"
    return result


def find_ftd_event(close_s, vol_s):
    rally = _find_rally_ftd(close_s, vol_s)
    event = rally["event"]
    return {
        "is_ftd": bool(event and event["sessions_since"] == 0),
        "event_date": event["date"] if event else None,
        "event_low": event["low"] if event else None,
        "days_since": rally["rally_day"],
        "message": (
            f"🔥 偵測到 FTD (反彈第 {event['rally_day']} 個交易日)！"
            if event and event["sessions_since"] == 0 else rally["message"]
        ),
    }


def evaluate_ftd_guard(close_s, vol_s, low_s):
    rally = _find_rally_ftd(close_s, vol_s, low_s)
    event = rally["event"]
    if event is None:
        return {
            "ftd_triggered": False, "ftd_valid": False,
            "ftd_day": None, "ftd_low": None, "ftd_price": None,
            "cancel_price": None, "status": "Observing",
            "days_since_ftd": None, "stop_investing": False,
            "message": rally["message"],
        }
    ftd_price = event["price"]
    cancel_price = ftd_price * FTD_CANCEL_PCT
    current_price = float(close_s.dropna().iloc[-1])
    invalidated = current_price < cancel_price
    evidence = (
        f"反彈起日 {event['rally_start'].strftime('%Y-%m-%d')}，"
        f"第 {event['rally_day']} 個交易日；"
        f"收盤 {event['previous_close']:.2f} → {ftd_price:.2f}"
        f"（{event['change_pct']:+.2f}%）；"
        f"量 {event['previous_volume']:,.0f} → {event['volume']:,.0f}"
    )
    return {
        "ftd_triggered": True, "ftd_valid": not invalidated,
        "ftd_day": event["date"], "ftd_low": event["low"],
        "ftd_price": ftd_price, "cancel_price": cancel_price,
        "status": "🔴 Failed" if invalidated else "Confirmed",
        "days_since_ftd": event["sessions_since"],
        "stop_investing": invalidated,
        "message": (
            f"🔴 FTD 失效：現價 {current_price:.2f} 跌破 FTD 容損價 {cancel_price:.2f}。"
            if invalidated else
            f"✅ FTD 有效：^GSPC 於 {event['date'].strftime('%Y-%m-%d')} 出現 FTD，"
            f"收盤 {ftd_price:.2f}，容損價 {cancel_price:.2f}。"
        ) + evidence,
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


def get_market_drawdown(close_frame, ticker):
    if close_frame is None or ticker not in close_frame.columns:
        return None
    prices = close_frame[ticker].dropna()
    if prices.empty:
        return None
    high = float(prices.tail(252).max())
    return (float(prices.iloc[-1]) / high - 1) * 100 if high > 0 else None


def plan_market_deployments(drawdowns, defense_pool, stock_headroom, vix_series, stop_investing=False):
    """Plan independent market signals against one shared, capped defense pool."""
    deployable_pool = max(0.0, defense_pool - 10.0)
    budget = min(deployable_pool, max(0.0, stock_headroom))
    plans = {}
    for ticker, label in (("VT", "全球 VT"), ("SPY", "美股 SPY"), ("0050.TW", "台股 0050.TW")):
        drawdown = drawdowns.get(ticker)
        level = get_drawdown_level(drawdown) if drawdown is not None else "資料不足"
        vix_state = evaluate_vix_ammo_delay(vix_series, level)
        requested = deployable_pool * LEVEL_ALLOCATIONS.get(level, 0) / 100.0
        if vix_state["delay"] or stop_investing:
            requested = 0.0
        plans[ticker] = {
            "label": label,
            "drawdown": drawdown,
            "level": level,
            "vix_delay": vix_state["delay"],
            "requested": requested,
        }

    requested_total = sum(plan["requested"] for plan in plans.values())
    scale = min(1.0, budget / requested_total) if requested_total > 0 else 0.0
    for plan in plans.values():
        plan["invest"] = plan["requested"] * scale
    return plans, deployable_pool, budget


def pending_exit_level(exit_signal):
    pending_actions = {"尚未觸發賣出", "續抱觀察", "未達 Level 5 停利"}
    level = exit_signal.get("觸發 Level（模擬）", "")
    return level if exit_signal.get("現階段是否賣出") in pending_actions and level.startswith("Level ") else None


def show_market_deployments(plans, exit_signals, defense_pool, deployable_pool, budget, stop_investing):
    rows = []
    for ticker, plan in plans.items():
        exit_signal = exit_signals[ticker]
        if plan["drawdown"] is None:
            status = "資料不足"
        elif plan["level"] == "未觸發":
            status = "目前未達 -20%"
        elif stop_investing:
            status = "FTD Guard 暫停"
        elif plan["vix_delay"]:
            status = "VIX 暫緩 Level 5"
        elif plan["invest"] <= 0:
            status = "無可部署資金"
        else:
            status = "可依 Level 部署"
        rows.append({
            "市場": plan["label"],
            "回檔": f"{plan['drawdown']:.2f}%" if plan["drawdown"] is not None else "資料不足",
            "當前進場 Level": plan["level"],
            "待賣 Level（模擬）": pending_exit_level(exit_signal) or "—",
            "建議投入": f"{plan['invest']:.1f}%",
            "狀態": status,
            "賣出判斷": exit_signal["現階段是否賣出"],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        f"同一防守資產池 {defense_pool:.1f}%｜保留 10%｜可部署池 {deployable_pool:.1f}%｜"
        f"股票上限後可部署 {budget:.1f}%｜各市場建議合計不超過可部署額。"
        "當前進場 Level 依最新回檔計算；待賣 Level 依最近一輪歷史觸發與模擬成本顯示，並非實際持倉。"
        "SPY 是美股專屬戰術訊號，基本配置目標仍依 VT／0050.TW 計算。"
    )


def show_dip_sell_guide():
    st.markdown("#### 💰 抄底後賣出建議｜CAN SLIM 一覽")
    sell_rules = pd.DataFrame([
        {"情境": "一般停損", "賣出／續抱條件": "收盤價低於該批買進成本 7–8%：停損賣出。", "適用": "每一批分開計算"},
        {"情境": "一般停利", "賣出／續抱條件": "獲利達該批成本 20–25%：考慮鎖利。", "適用": "每一批分開計算"},
        {"情境": "快速上漲例外", "賣出／續抱條件": "買進後 1–3 週內上漲 20% 以上：續抱；收盤跌破 10 日均線時再判斷是否賣出。", "適用": "突破一般 20–25% 停利規則"},
        {"情境": "Level 5 特別規則", "賣出／續抱條件": "獲利達該批成本 50% 才進入停利賣出；若先觸發 7–8% 停損，仍依停損規則處理。", "適用": "本程式的 Level 5 規則"},
    ])
    st.table(sell_rules)
    st.caption("請以每筆成交成本各自計算報酬；目前程式尚未記錄各批買進成本，因此這裡提供規則摘要，不能代替個人持倉的即時賣出價。")

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
        return "🟢 Normal（多市場 Normal）"
    if bullish_count == 2:
        return "🟡 Caution（多市場 Caution）"
    return "🔴 Crisis（多市場 Crisis）"

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
    """
    Truth 6.0 Master Pro: 
    中樞 60% / 動態範圍 50-80% / 10% 生存金強制鎖定
    """
    # 1. 決定股票中樞 (Pivot) - 階梯式 Glide Path
    if ytr_val >= 25:      # 距退休 > 25 年 (年輕人)
        pivot_stock = 85.0
    elif ytr_val >= 20:    # 距退休 20-25 年
        pivot_stock = 80.0
    elif ytr_val >= 15:    # 距退休 15-20 年
        pivot_stock = 70.0
    elif ytr_val > 10:     # 距退休 10-15 年 (中生代)
        pivot_stock = 60.0
    elif ytr_val > 5:
        pivot_stock = 55.0
    elif ytr_val > 0:
        pivot_stock = 45.0
    else:  # 已退休
        pivot_stock = 40.0

    # 基礎配置固定 (辯論共識：10% 黃金, 10% 現金)
    base_gold = 10.0
    base_cash = 10.0
    
    # 處理年輕人高股票中樞導致的空間擠壓，避免 base_bond 變成負數
    if pivot_stock + base_gold + base_cash > 100.0:
        excess = pivot_stock + base_gold + base_cash - 100.0
        base_gold = max(0.0, base_gold - excess) # 黃金優先讓出空間
        base_bond = 0.0
    else:
        base_bond = 100.0 - pivot_stock - base_gold - base_cash
    
    # 2. 市場模式偏移 (Tactical Tilt)
    # 計算年齡壓縮係數 (Age Compression)，用於平時的 Tilt 縮放
    age_compression = max(0.2, (age_val - 20) / 40)
    regime_tilt = 0.0
    tilt_reason = "Normal"

    if "Normal" in mode_label:
        # 高點防禦：若接近高點則不偏移或小幅減碼
        regime_tilt = 0.0 # 保持中樞
    elif "Caution" in mode_label:
        # 警戒模式：縮減股票
        regime_tilt = -5.0 * age_compression
        if ftd_confirmed: # FTD 特赦
            regime_tilt += 5.0
            tilt_reason = "Caution (FTD Amnesty)"
    
    # 3. 動態風險上限 (Dynamic Risk Cap)
    # 年輕人適用較寬鬆的上限，中老年後依據 100-age 遞減
    age_limit = 100.0 - age_val
    ytr_limit = 50.0 + ytr_val
    safe_cap = min(age_limit, ytr_limit)
    
    if drawdown_val <= -20:
        # 🔴 掠食者模式：危機時上限釋放 (最高 90%，需留 10% 現金)
        max_stock = 90.0 - base_gold
        mode_state = "🔴 掠食者模式 (上限釋放)"
        # 危機時不執行 age_compression 限制，允許直接加碼至上限
        if "Crisis" in mode_label:
            regime_tilt = 10.0 
    else:
        # 🟢 守成模式：不再強制用 min(pivot, safe_cap)，而是允許 pivot 達到 safe_cap
        # 這樣 25 歲的人可以持有 pivot 的 85%，或受限於 safe_cap 的 75%
        max_stock = min(pivot_stock, safe_cap)
        mode_state = "🟢 守成模式 (嚴格限額)"

    # 4. 溢位處理 (Overflow Handling)
    stock_raw = pivot_stock + regime_tilt
    stk_f = min(stock_raw, max_stock)
    overflow = max(0.0, stock_raw - max_stock)

    # 5. 殘餘正規化 (Residual Normalization) - 核心修正
    # 確保股票比例 stk_f 絕對不被二次縮放
    current_bond = base_bond
    current_gold = base_gold
    current_cash = base_cash + overflow

    if bond_protection_on:
        current_cash += current_bond
        current_bond = 0.0

    # 計算非股票資產的總合與剩餘空間
    others_sum = current_bond + current_gold + current_cash
    remaining_space = 100.0 - stk_f
    
    if others_sum > 0:
        bnd_f = (current_bond / others_sum) * remaining_space
        gld_f = (current_gold / others_sum) * remaining_space
        csh_f = (current_cash / others_sum) * remaining_space
    else:
        bnd_f, gld_f, csh_f = 0.0, current_gold, remaining_space - current_gold

    # 6. 生存金最終檢核 (確保 Cash + Bond >= 10.0)
    survival_fund = bnd_f + csh_f
    if survival_fund < 10.0:
        gap = 10.0 - survival_fund
        stk_f -= gap
        csh_f += gap

    # 計算一些 UI 需要的輔助數值
    defense_after = bnd_f + csh_f

    explain = {
        "tilt_reason": tilt_reason,
        "mode_state": mode_state,
        "safe_cap": safe_cap,
        "max_stock": max_stock,
        "bond_reason": f"中樞 {pivot_stock}% | 生存金底線 10.0%",
        "stock_tilt": regime_tilt,
        "mode_label": mode_label,
        "defense_total": base_bond + base_cash,
        "defense_after": defense_after,
        "total_alloc": 100.0,
        "alloc_gap": 0.0
    }

    return stk_f, bnd_f, gld_f, csh_f, pivot_stock, base_bond, base_cash, regime_tilt, age_compression, explain

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
        if mkt_data.get("deployment_pct", 0.0) > 0:
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
    if data.empty or len(data) == 0:
        return create_gauge(
            0,
            f"{label} - No data",
            -60,
            5,
            [
                {"range": [-50, -40], "color": "#7F1D1D"},
                {"range": [-40, -35], "color": "#991B1B"},
                {"range": [-35, -30], "color": "#B91C1C"},
                {"range": [-30, -25], "color": "#DC2626"},
                {"range": [-25, -20], "color": "#EF4444"},
                {"range": [-20, -15], "color": "#F97316"},
                {"range": [-15, -10], "color": "#F59E0B"},
                {"range": [-10, -5], "color": "#EAB308"},
                {"range": [-5, 0], "color": "#84CC16"},
            ],
            suffix="%",
        )

    try:
        curr = data.iloc[-1]
        high = data.tail(252).max()
        ma200_series = data.rolling(200).mean().dropna()
        if ma200_series.empty:
            ma200 = curr
        else:
            ma200 = ma200_series.iloc[-1]
        dd = (curr - high) / high * 100 if high and not pd.isna(high) else 0.0
        ma200_rel = (ma200 - high) / high * 100 if high and not pd.isna(high) and not pd.isna(ma200) else 0.0
    except (IndexError, KeyError):
        return create_gauge(
            0,
            f"{label} - No data",
            -60,
            5,
            [
                {"range": [-50, -40], "color": "#7F1D1D"},
                {"range": [-40, -35], "color": "#991B1B"},
                {"range": [-35, -30], "color": "#B91C1C"},
                {"range": [-30, -25], "color": "#DC2626"},
                {"range": [-25, -20], "color": "#EF4444"},
                {"range": [-20, -15], "color": "#F97316"},
                {"range": [-15, -10], "color": "#F59E0B"},
                {"range": [-10, -5], "color": "#EAB308"},
                {"range": [-5, 0], "color": "#84CC16"},
            ],
            suffix="%",
        )

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


def drawdown_episode_summary(ticker, label, df, threshold=-20.0):
    """Summarize the latest drawdown episode crossing the configured threshold."""
    if df is None or ticker not in df.columns:
        return f"{label}：資料不足"

    prices = pd.to_numeric(df[ticker], errors="coerce").dropna()
    if prices.empty:
        return f"{label}：資料不足"

    drawdown = (prices / prices.rolling(252, min_periods=1).max() - 1) * 100
    below_threshold = drawdown <= threshold
    crossed_below = below_threshold & ~below_threshold.shift(1, fill_value=False)
    crossed_below.iloc[0] = False

    crossing_positions = list(drawdown.index[crossed_below])
    left_censored = bool(below_threshold.iloc[0])
    if left_censored:
        crossing_positions.insert(0, drawdown.index[0])

    if not crossing_positions:
        return f"{label}：近兩年未跌破 {threshold:.0f}%"

    episode_start = crossing_positions[-1]
    start_pos = drawdown.index.get_loc(episode_start)
    recovery_positions = [
        pos for pos in range(start_pos + 1, len(drawdown))
        if not below_threshold.iloc[pos]
    ]
    episode_end = recovery_positions[0] if recovery_positions else len(drawdown)
    episode = drawdown.iloc[start_pos:episode_end]
    max_drawdown = float(episode.min())
    max_date = pd.Timestamp(episode.idxmin()).strftime("%Y-%m-%d")

    if episode_start == drawdown.index[0] and left_censored and len(crossing_positions) == 1:
        start_label = f"資料起點已低於 {threshold:.0f}%（{pd.Timestamp(episode_start).strftime('%Y-%m-%d')} 起）"
    else:
        start_label = f"最近跌破 {threshold:.0f}%：{pd.Timestamp(episode_start).strftime('%Y-%m-%d')}"

    episode_label = "本輪最大回檔" if bool(below_threshold.iloc[-1]) else "該輪最大回檔"
    return f"{label}｜{start_label}｜{episode_label}：{max_drawdown:.1f}%（{max_date}）"


def summarize_dip_exit(close_series, market_timezone):
    """Evaluate the deepest tranche of the most recent -20% drawdown episode."""
    empty = {"最近最大跌幅": "資料不足", "觸發 Level（模擬）": "無法判斷", "現階段是否賣出": "無法判斷", "原因": "缺少收盤價資料"}
    if close_series is None:
        return empty
    prices = pd.to_numeric(close_series, errors="coerce").dropna()
    if prices.empty:
        return empty

    index = pd.DatetimeIndex(pd.to_datetime(prices.index))
    session_dates = index.tz_convert(market_timezone).date if index.tz is not None else index.date
    today = pd.Timestamp.now(tz=market_timezone).date()
    completed = [day < today for day in session_dates]
    prices = prices.iloc[completed]
    if prices.empty:
        return empty

    drawdown = (prices / prices.rolling(252, min_periods=1).max() - 1) * 100
    below_20 = drawdown <= -20.0
    crossings = below_20 & ~below_20.shift(1, fill_value=False)
    starts = list(np.flatnonzero(crossings.to_numpy()))
    if bool(below_20.iloc[0]):
        starts.insert(0, 0)
    if not starts:
        return {
            "最近最大跌幅": f"{float(drawdown.min()):.2f}%（近兩年）",
            "觸發 Level（模擬）": "未觸發",
            "現階段是否賣出": "無抄底部位可判斷",
            "原因": "載入期間未曾跌至 -20%；未提供實際買進成本。",
        }

    start = starts[-1]
    recovery = np.flatnonzero((~below_20.iloc[start + 1:]).to_numpy())
    end = start + 1 + int(recovery[0]) if len(recovery) else len(drawdown)
    episode = drawdown.iloc[start:end]
    deepest = float(episode.min())
    level = get_crisis_level(deepest)
    threshold = -20.0 - 5.0 * (level - 1)
    entry_offset = int(np.flatnonzero((episode <= threshold).to_numpy())[0])
    entry_pos = start + entry_offset
    entry_price = float(prices.iloc[entry_pos])
    latest_price = float(prices.iloc[-1])
    gain = (latest_price / entry_price - 1) * 100
    entry_date = pd.Timestamp(prices.index[entry_pos]).strftime("%Y-%m-%d")
    fast_window = prices.iloc[entry_pos + 1:entry_pos + 16]
    fast_rise = bool((fast_window >= entry_price * 1.20).any())
    ma10 = float(prices.tail(10).mean()) if len(prices) >= 10 else None
    basis = f"以 {entry_date} 收盤 {entry_price:.2f} 模擬 Level {level} 進場；目前相對該價 {gain:+.1f}%"

    if gain <= -8.0:
        action, reason = "是，停損", "已低於模擬買進價 8%。"
    elif gain <= -7.0:
        action, reason = "進入停損區", "低於模擬買進價 7–8%，應檢視停損。"
    elif level == 5:
        action = "是，Level 5 停利" if gain >= 50.0 else "未達 Level 5 停利"
        reason = "Level 5 依特別規則以 +50% 為停利門檻。"
    elif fast_rise and ma10 is not None:
        action = "檢視賣出" if latest_price < ma10 else "續抱觀察"
        reason = f"買後 15 個交易日內曾上漲 20%；目前收盤 {'跌破' if latest_price < ma10 else '仍高於'} 10 日均線 {ma10:.2f}。"
    elif gain >= 20.0:
        action, reason = "考慮分批賣出", "已達一般 20–25% 鎖利區。"
    else:
        action, reason = "尚未觸發賣出", "未達 7–8% 停損或 20–25% 停利條件。"

    return {
        "最近最大跌幅": f"{deepest:.2f}%（{pd.Timestamp(episode.idxmin()).strftime('%Y-%m-%d')}）",
        "觸發 Level（模擬）": f"Level {level}",
        "現階段是否賣出": action,
        "原因": f"{basis}；{reason}",
    }

# --- 5. 數據解析 ---
is_ftd, ftd_msg = False, "觀察中"
ftd_guard = evaluate_ftd_guard(df_close["^GSPC"], df_vol["SPY"], df_low["^GSPC"])
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
    cpi_yoy, cpi_actual_yoy, core_pce_yoy, core_pce_actual_yoy, cpi_prev, core_pce_prev, rate_val, rate_prev, spread_val, cpi_t, core_pce_t, rate_t = 3.2, 3.2, 3.0, 3.0, 3.2, 3.0, 5.25, 5.25, 0.5, "Up", "Up", "Up"
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
vt_drawdown = get_market_drawdown(df_close, "VT")
vt_dd_v = vt_drawdown if vt_drawdown is not None else 0.0
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
    vol_series = df_vol[ticker].dropna() if df_vol is not None and ticker in df_close.columns else pd.Series(dtype=float)
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
remaining_cash = csh_p
stock_headroom = max(0.0, MAX_STOCK_WEIGHT - stk_p)
market_drawdowns = {
    "VT": vt_drawdown,
    "SPY": get_market_drawdown(df_close, "SPY"),
    "0050.TW": get_market_drawdown(df_close, "0050.TW"),
}
market_deployments, deployable_defense_pool, deployment_budget = plan_market_deployments(
    market_drawdowns, defense_pool, stock_headroom, df_vix, ftd_guard.get("stop_investing", False)
)
market_exit_signals = {
    ticker: summarize_dip_exit(df_close[ticker] if ticker in df_close.columns else None, timezone)
    for ticker, timezone in (("VT", "America/New_York"), ("SPY", "America/New_York"), ("0050.TW", "Asia/Taipei"))
}
suggested_invest = sum(plan["invest"] for plan in market_deployments.values())
highest_level = max(get_crisis_level(dd) for dd in market_drawdowns.values() if dd is not None) if any(
    dd is not None for dd in market_drawdowns.values()
) else 0
pending_levels = [
    int(level.split()[-1])
    for signal in market_exit_signals.values()
    for level in [pending_exit_level(signal)]
    if level
]
highest_pending_level = max(pending_levels, default=0)
pending_label = f"L{highest_pending_level}" if highest_pending_level else "—"
level_metric = f"進 L{highest_level}｜待賣 {pending_label}"
level_summary = "｜".join(
    f"{ticker} {plan['level'].replace('Level ', 'L') if plan['level'] != '未觸發' else 'L0'}"
    for ticker, plan in market_deployments.items()
)
pending_summary = "｜".join(
    f"{ticker} {(level or '—').replace('Level ', 'L')}"
    for ticker, signal in market_exit_signals.items()
    for level in [pending_exit_level(signal)]
)
deployed_pct = max(0.0, 100.0 - defense_pool - gld_p)
cash_lock_status = "🟢 自行判斷"

# --- 6. 主決策區 ---
if view_mode == "Master":
    tension_c1, tension_c2, tension_c3, tension_c4 = st.columns(4)
    regime_light = "🟢" if "Normal" in decision_regime else "🟡" if "Caution" in decision_regime else "🔴"
    regime_metric_label = "Normal" if "Normal" in decision_regime else "Caution" if "Caution" in decision_regime else "Crisis"
    regime_short_reason = "FTD/趨勢/廣度綜合後的最終模式"
    tension_c1.metric(
        "Decision Regime",
        f"{regime_light} {regime_metric_label}",
        current_mode,
    )
    # 根據目前模式產生對應的操作建議 (與 caption 寫在一起)
    if suggested_invest > 0:
        guide_i = "VT／SPY／0050.TW 有市場專屬抄底訊號，依各自 Level 與共用資金上限部署。"
    elif highest_pending_level > 0:
        guide_i = f"目前未觸發新買；歷史模擬進場尚待賣出訊號：{pending_summary}。"
    elif "Normal" in decision_regime:
        guide_i = "新入金按目標比例配置，維持系統平衡。"
    elif "Caution" in decision_regime:
        guide_i = "停止提高股票曝險，新資金會留在「現金池」觀望。"
    else:
        guide_i = "主動將現金池資金按 Level 階梯轉入股市抄底。"
    if bond_protection_on:
        guide_i += "｜高通膨環境下，債券權重自動由現金承接。"

    tension_c1.caption(f"{decision_regime}｜{regime_short_reason}｜📘 {guide_i}")

    tension_c2.metric("抄底 Level", level_metric, f"新投入 {suggested_invest:.1f}%")
    tension_c2.caption(f"當前進場：{level_summary}｜模擬待賣：{pending_summary}｜共用可部署額 {deployment_budget:.1f}%")

    tension_c3.metric("BLS/FRED Clock Phase", macro_background_bls["phase"])
    tension_c3.caption(
        f"PMI={macro_background_bls['PMI']}｜CPI={cpi_actual_yoy:.2f}%({cpi_t})｜Core PCE={bls_core_pce_yoy:.2f}%({core_pce_t})｜Rate={macro_background_bls['Rate']}"
    )

    tension_c4.metric("Cleveland Fed Clock Phase", macro_background_nowcast["phase"])
    tension_c4.caption(
        f"PMI={macro_background_nowcast['PMI']}｜CPI={cleveland_cpi_yoy:.2f}%({cpi_t})｜Core PCE={cleveland_core_pce_yoy:.2f}%({core_pce_t})｜Rate={macro_background_nowcast['Rate']}"
    )

st.markdown("### 🧠 Layer 1：市場狀態")
with st.expander("❓ 這裡怎麼判斷？", expanded=False):
    st.markdown(
        f"""
**Layer 1 的目的：先決定市場風險模式，再決定能不能提高股票曝險。**

**判斷順序**
1. 先看 FTD Guard：若已失效，或價格跌破 200MA，決策壓回 `Caution`。
2. 再看核心指標：
   - Drawdown ≤ -20% 或 VIX ≥ 30 → `Crisis` (進入掠食者模式)
   - 價格 < 200MA → `Caution`
   - 其餘 → `Normal`
3. 最終 `Decision Regime` 依據市場廣度與主軸取較保守結果。

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
    if suggested_invest > 0:
        st.info(f"👉 **市場專屬抄底**：VT／SPY／0050.TW 各依自身回檔 Level 部署，合計建議投入 {suggested_invest:.1f}%。")
    elif highest_pending_level > 0:
        st.info(f"👉 **模擬待賣觀察**：{pending_summary}；目前沒有新的抄底投入建議。")
    elif current_mode == "🔴 危機":
        st.error("👉 **Crisis**：目前沒有可部署的市場專屬抄底額，請查看各市場 Level 與暫停原因。")
    elif current_mode == "🟡 警戒":
        st.warning("👉 **Caution**：停止提高股票曝險，新資金會留在「現金池」觀望。")
    else:
        st.success("👉 **Normal**：新入金按目標比例配置，維持系統平衡，僅在偏離 band 時再平衡。")
    beginner_alloc = pd.DataFrame({
        "資產": ["股票", "債券", "黃金", "現金"],
        "配置": [f"{stk_p:.1f}%", f"{bnd_p:.1f}%", f"{gld_p:.1f}%", f"{csh_p:.1f}%"],
    })
    st.table(beginner_alloc)
    show_market_deployments(market_deployments, market_exit_signals, defense_pool, deployable_defense_pool, deployment_budget, ftd_guard.get("stop_investing", False))
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
                st.plotly_chart(create_drawdown_gauge("VT", "VT (全球股市)", df_close), use_container_width=True)
                st.caption(drawdown_episode_summary("VT", "VT", df_close))
            with d2:
                st.plotly_chart(create_drawdown_gauge("0050.TW", "0050.TW (台灣50)", df_close), use_container_width=True)
                st.caption(drawdown_episode_summary("0050.TW", "0050.TW", df_close))
            with d3:
                st.plotly_chart(create_drawdown_gauge("QQQ", "QQQ (納斯達克)", df_close), use_container_width=True)
                st.caption(drawdown_episode_summary("QQQ", "QQQ", df_close))
            with d4:
                st.plotly_chart(create_drawdown_gauge("SPY", "SPY (美股)", df_close), use_container_width=True)
                st.caption(drawdown_episode_summary("SPY", "SPY", df_close))

            st.caption("統計範圍：使用目前載入的近兩年日收盤資料，並以過去 252 個交易日最高收盤價計算回檔。")

            if view_mode == "Pro":
                st.markdown("#### 🎁 VT／SPY／0050.TW 專屬抄底引擎")
                engine_c1, engine_c2, engine_c3, engine_c4 = st.columns(4)
                engine_c1.metric("進場／待賣 Level", level_metric)
                engine_c2.metric("本次部署額 %", f"{suggested_invest:.1f}%")
                engine_c3.metric("可部署上限 %", f"{deployment_budget:.1f}%")
                engine_c4.metric("FTD", "✅ Valid" if ftd_guard.get("ftd_valid") else "🟡 Guarded")
                show_market_deployments(market_deployments, market_exit_signals, defense_pool, deployable_defense_pool, deployment_budget, ftd_guard.get("stop_investing", False))
                show_dip_sell_guide()

if ftd_guard.get("stop_investing"):
    st.warning(f"FTD 防錯機制啟動：{ftd_guard['message']}")
if bond_protection_on:
    st.info("債券保護開關啟動：CPI > 3.5% 且 FEDFUNDS 上行，債券目標權重以現金取代。")

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
- 當前進場 Level：{level_summary}
- 模擬待賣 Level：{pending_summary}
"""
        )
    if suggested_invest > 0:
        action_msg = f"市場專屬抄底｜{level_summary}｜合計投入 {suggested_invest:.1f}%"
    elif highest_pending_level > 0:
        action_msg = f"模擬待賣觀察｜{pending_summary}｜目前無新買入額"
    elif current_mode == "🔴 危機":
        action_msg = "危機模式｜目前無可部署額｜查看市場 Level 與暫停原因"
    elif current_mode == "🟡 警戒":
        action_msg = "警戒避震｜停止主動買入股票｜新資金停留現金池"
    elif exit_ready:
        action_msg = "脫離危機區｜回到中樞配置｜啟動正常再平衡"
    else:
        action_msg = "正常航行｜維持配置比例｜必要時再平衡"
    st.markdown(f"### 🎯 指令行動：**{action_msg}**")

    if suggested_invest > 0:
        st.info("🔵 市場專屬 Level 已觸發：依 VT／SPY／0050.TW 各自回檔部署，共用防守資產池並保留 10%。")
    elif highest_pending_level > 0:
        st.info(f"🟠 最近一輪模擬進場尚未觸發賣出：{pending_summary}。請以實際成交紀錄確認持倉。")
    elif current_mode == "🔴 危機":
        st.error("🔴 危機模式：股票上限釋放至 80%，依回檔階梯主動消耗現金部署股市。")
    elif current_mode == "🟡 警戒":
        st.warning("🟡 警戒模式：停止主動提高股票曝險，保持目前持倉，新入金的資金會停留在「現金池」，而不是投入股市。")
    else:
        st.success("🟢 正常模式：維持目標配置，新資金應依比例配置，若偏離 band 則執行再平衡。")


    if exit_ready and view_mode in ["Pro", "Master"]:
        st.success("🛑 Exit Rule 啟動：價格已站上 200MA 且脫離危機區，停止危機部署並回到再平衡框架。")

    if abs(stk_p - b_stk_v) < 1 and current_mode != "🔴 危機" and view_mode in ["Pro", "Master"]:
        st.info("⚠️ 提醒：目前偏離不大，避免過度交易。")

with nav2:
    st.info(f"**配置摘要**：股票 {stk_p:.1f}% / 債券 {bnd_p:.1f}% / 黃金 {gld_p:.1f}% / 現金 {csh_p:.1f}%")
    st.caption("❌ 黃金 5% 永鎖，不做賣出調整。")

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
- VT、SPY、0050.TW 各自依近 252 個交易日最高收盤價判斷 Level。
- 各市場原始建議額 =（防守資產池 - 10%）× 對應 Level 比例；合計超過可部署額時按比例縮減。
- 合計受股票上限約束；FTD Guard 停止投資或該市場 Level 5 遇 VIX 創高時暫停部署。

**你現在看到這個結果，是因為**
- 當前進場 Level：{level_summary}
- 模擬待賣 Level：{pending_summary}
- 合計建議可動用：{suggested_invest:.1f}%
- FTD Guard：{ftd_msg}
- VIX Ammo：{vix_ammo_state['message']}
"""
        )
    engine_c1, engine_c2, engine_c3, engine_c4 = st.columns(4)
    engine_c1.metric("進場／待賣 Level", level_metric)
    engine_c2.metric("本次部署額 %", f"{suggested_invest:.1f}%")
    engine_c3.metric("可部署上限 %", f"{deployment_budget:.1f}%")
    engine_c4.metric("FTD", "✅ Valid" if ftd_guard.get("ftd_valid") else "🟡 Guarded")
    show_market_deployments(market_deployments, market_exit_signals, defense_pool, deployable_defense_pool, deployment_budget, ftd_guard.get("stop_investing", False))
    show_dip_sell_guide()

    # --- 威廉・歐尼爾單日漲幅與成交量檢視 ---
    st.divider()
    st.markdown("#### 📈 威廉・歐尼爾單日漲幅、成交量與抄底賣出檢視")
    st.caption(
        "比較最近一個已完成交易日與前一交易日：收盤漲幅是否達 1.25%／1.5%，"
        "以及成交量是否增加。完整 Follow-Through Day（FTD）還需符合反彈日數等條件。"
    )

    oneil_assets = [
        ("S&P 500", "^GSPC", "SPY", "America/New_York", "S&P 500 指數", "SPY（成交量代理）"),
        ("VT", "VT", "VT", "America/New_York", "VT", "VT"),
        ("0050.TW", "0050.TW", "0050.TW", "Asia/Taipei", "0050.TW", "0050.TW"),
    ]
    oneil_rows = []
    for asset_name, price_ticker, volume_ticker, market_timezone, price_label, volume_label in oneil_assets:
        price_series = df_close[price_ticker] if price_ticker in df_close.columns else None
        volume_series = df_vol[volume_ticker] if volume_ticker in df_vol.columns else None
        signal = evaluate_oneil_daily_rule(price_series, volume_series, market_timezone)
        exit_signal = summarize_dip_exit(price_series, market_timezone)

        if signal is None:
            oneil_rows.append({
                "標的": asset_name,
                "交易日": "資料不足",
                **exit_signal,
                "單日漲幅": "—",
                "成交量比較（當日 vs 前日）": "—",
                "成交量條件": "無法判斷",
                "1.25% + 量增": "無法判斷",
                "1.5% + 量增": "無法判斷",
                "價格來源": price_label,
                "成交量來源": volume_label,
            })
            continue

        gain_125 = signal["change_pct"] >= 1.25
        gain_150 = signal["change_pct"] >= 1.5
        volume_text = f'{signal["volume"]:,.0f} vs {signal["previous_volume"]:,.0f}'
        oneil_rows.append({
            "標的": asset_name,
            "交易日": signal["date"],
            **exit_signal,
            "單日漲幅": f'{signal["change_pct"]:+.2f}%',
            "成交量比較（當日 vs 前日）": volume_text,
            "成交量條件": "✅ 放量" if signal["volume_up"] else "❌ 未放量",
            "1.25% + 量增": "✅ 符合" if gain_125 and signal["volume_up"] else "❌ 未符合",
            "1.5% + 量增": "✅ 符合" if gain_150 and signal["volume_up"] else "❌ 未符合",
            "價格來源": price_label,
            "成交量來源": volume_label,
        })

    st.dataframe(pd.DataFrame(oneil_rows), use_container_width=True, hide_index=True)
    st.caption(
        "標普 500 指數沒有可直接比較的個股成交量，因此以 SPY ETF 成交量作代理；"
        "漲幅與回檔使用 ^GSPC 指數收盤價。量能僅在同一標的內與前一交易日比較。"
        "最近最大跌幅取載入期間最近一次跌破 -20% 的回檔事件；Level 與賣出判斷以該事件最深級別首次觸發日的收盤價模擬，"
        "快速上漲的 1–3 週以買後 15 個交易日近似。這不是實際成交或持倉；"
        "若需判斷真實持倉，仍須提供每筆買進日期與成本；S&P 500 指數不能直接交易。"
    )


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
    g1, g2, g3, g4, g5 = st.columns(5)
    with g1:
        steps = [{"range": [0, 2], "color": "#A7F3D0"}, {"range": [2, 3], "color": "#FDE68A"}, {"range": [3, 10], "color": "#FECACA"}]
        st.plotly_chart(create_gauge(cpi_actual_yoy, f"CPI YoY 通膨<br><span style='font-size:11px;color:gray'>來源: FRED｜趨勢: {cpi_t}</span>", 0, 8, steps), use_container_width=True)
    with g2:
        steps = [{"range": [0, 2], "color": "#A7F3D0"}, {"range": [2, 4], "color": "#FDE68A"}, {"range": [4, 8], "color": "#FECACA"}]
        st.plotly_chart(create_gauge(rate_val, f"基準利率 vs 中性區間<br><span style='font-size:11px;color:gray'>趨勢: {rate_t}</span>", 0, 8, steps), use_container_width=True)
    with g3:
        steps = [{"range": [-3, 0], "color": "#FECACA"}, {"range": [0, 1], "color": "#FDE68A"}, {"range": [1, 4], "color": "#A7F3D0"}]
        st.plotly_chart(create_gauge(spread_val, "利差 (10Y-2Y)", -2, 4, steps, suffix=""), use_container_width=True)
    with g4:
        steps = [{"range": [0, 15], "color": "#BFDBFE"}, {"range": [15, 20], "color": "#A7F3D0"}, {"range": [20, 30], "color": "#FDE68A"}, {"range": [30, 60], "color": "#FECACA"}]
        st.plotly_chart(create_gauge(vix_latest, "VIX 恐慌指數", 0, 60, steps, suffix=""), use_container_width=True)
    with g5:
        fg_steps = [
            {"range": [0, 25], "color": "#166534"},
            {"range": [25, 45], "color": "#65A30D"},
            {"range": [45, 55], "color": "#CA8A04"},
            {"range": [55, 75], "color": "#EA580C"},
            {"range": [75, 100], "color": "#991B1B"},
        ]
        fg_delta_ref = cnn_fg_data["previous_close"] if cnn_fg_data is not None else None
        st.plotly_chart(create_gauge(
            fg_score_input,
            f"CNN Fear & Greed<br><span style='font-size:11px;color:gray'>{fg_classification['emoji']} {fg_classification['label']}｜{fg_classification['action_short']}</span>",
            0, 100, fg_steps, suffix="", ref=fg_delta_ref
        ), use_container_width=True)

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
            st.dataframe(display_df.tail(10).sort_index(ascending=False), use_container_width=True)
        else:
            st.warning("目前使用手動輸入模式，無歷史數據可顯示。")

    # --- 📌 市場情緒錶：CNN Fear & Greed ---
    st.divider()
    st.subheader("📌 市場情緒錶：CNN Fear & Greed")

    # 計算交叉驗證
    fg_cross_results, fg_cross_summary = evaluate_sentiment_cross(
        fg_score_input, vix_latest, cpi_actual_yoy, rate_val, spread_val
    )

    fg_mc1, fg_mc2, fg_mc3 = st.columns(3)
    with fg_mc1:
        fg_delta_val = fg_score_input - cnn_fg_data["previous_close"] if cnn_fg_data is not None else None
        st.metric(
            f"{fg_classification['emoji']} CNN Fear & Greed",
            f"{fg_score_input:.0f} ({fg_classification['label']})",
            delta=f"{fg_delta_val:+.0f} vs 昨日" if fg_delta_val is not None else None,
        )
    with fg_mc2:
        st.metric(
            "建議動作",
            fg_classification["action_short"],
            delta=fg_classification["action"],
            delta_color="off",
        )
    with fg_mc3:
        # 將長文字轉換為簡短的狀態標籤，保持排版整潔
        short_status = "ℹ️ 維持判斷"
        if "📈" in fg_cross_summary: short_status = "📈 強烈買點"
        elif "📉" in fg_cross_summary: short_status = "📉 強烈防禦"
        elif "⚠️" in fg_cross_summary: short_status = "⚠️ 需再觀察"

        st.metric(
            "情緒確認層",
            short_status,
            help=fg_cross_summary  # 將完整訊息放入 Hover 提示框中
        )

    # 歷史參考
    if cnn_fg_data is not None:
        fg_hist_cols = st.columns(4)
        hist_data = [
            ("昨日收盤", cnn_fg_data.get("previous_close")),
            ("一週前", cnn_fg_data.get("one_week_ago")),
            ("一月前", cnn_fg_data.get("one_month_ago")),
            ("一年前", cnn_fg_data.get("one_year_ago")),
        ]
        for col, (label, val) in zip(fg_hist_cols, hist_data):
            if val is not None:
                hist_cls = classify_fear_greed(val)
                col.metric(label, f"{val:.0f}", delta=f"{hist_cls['emoji']} {hist_cls['label']}")
            else:
                col.metric(label, "N/A")

    # 交叉對照摘要表 (顯示當前的對照結果)
    st.markdown("**情緒交叉對照（CNN vs 現有指標）**")
    fg_cross_df = pd.DataFrame(fg_cross_results)
    fg_cross_df.columns = ["對照組", "訊號", "說明"]
    st.dataframe(fg_cross_df, use_container_width=True, hide_index=True)

    # 判斷機制與對照表 (收納至 Expander)
    with st.expander("📋 情緒確認層詳細判斷機制與對照表", expanded=False):
        st.markdown("#### 直覺分級對照表")
        fg_ref_df = pd.DataFrame([
            {"分數區間": "0 - 24", "燈號": "🟢 深綠", "分類": "Extreme Fear", "建議": "逢低佈局機會（買點）"},
            {"分數區間": "25 - 44", "燈號": "🟡 綠黃", "分類": "Fear", "建議": "觀望偏多"},
            {"分數區間": "45 - 54", "燈號": "🟡 黃燈", "分類": "Neutral", "建議": "觀望為主"},
            {"分數區間": "55 - 74", "燈號": "🟠 橙燈", "分類": "Greed", "建議": "減碼觀望"},
            {"分數區間": "75 - 100", "燈號": "🔴 深紅", "分類": "Extreme Greed", "建議": "減碼或保守（減碼）"},
        ])
        st.dataframe(fg_ref_df, use_container_width=True, hide_index=True)

        st.markdown(
            """
#### 交叉對照判斷機制

**【CNN vs VIX 波動率】**
- **Extreme Fear + VIX 高 (>= 30)：** 📈 情緒恐慌，逢低佈局核心持股機會
- **Extreme Greed + VIX 低 (< 20)：** 📉 風險加劇，建議提高防守資產、降低股票比重

**【CNN vs CPI 通膨】**
- **Extreme Greed + 高通膨 (> 3.5%)：** 🔴 泡沫風險，建議保守配置
- **Extreme Fear + 低通膨 (< 2.5%)：** 🟢 基本面支撐下的恐慌超賣，進場機會

**【CNN vs 利率】**
- **Extreme Greed + 高利率 (> 5.0%)：** 🔴 資金成本高且情緒過熱，風險偏高
- **Extreme Fear + 低利率 (< 3.0%)：** 🟢 寬鬆環境下的恐慌，逢低機會

**【CNN vs Spread 利差】**
- **Extreme Greed + 殖利率倒掛 (< 0)：** 🔴 衰退風險與貪婪並存，極度警戒
- **Extreme Fear + 正常利差 (> 1.0)：** 🟢 經濟結構正常下的恐慌超賣
            """
        )
    if cnn_fg_data is not None:
        st.caption(f"資料來源：[CNN Fear & Greed Index]({cnn_fg_data['source_url']})")
    else:
        st.caption("⚠️ CNN 資料抓取失敗，目前使用手動輸入值。")


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
        "vix_delay": any(plan["vix_delay"] for plan in market_deployments.values()),
        "deployment_pct": suggested_invest,
        "vix": vix_latest,
        "stock_headroom": stock_headroom,
    }

    trigger_action_map = {
        "BUY_TO_TARGET": ("🟡 Buy", "買回目標配置"),
        "SELL_TO_TARGET": ("🔴 Sell", "賣回目標配置"),
        "HOLD": ("🟢 Hold", "不動"),
        "TACTICAL_DEPLOYMENT": ("🔵 市場專屬抄底", "依市場 Level 動用防守資產"),
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
            reason_bits.append(f"現金池依 VT／SPY／0050.TW 專屬 Level 做事件驅動判斷；合計建議投入 {suggested_invest:.1f}%")
        else:
            reason_bits.append(f"偏離 {deviation:+.1f}% vs band ±{band:.1f}%")
            if deviation < 0:
                reason_bits.append("低於目標且超出 band，給出 BUY 建議")
            elif deviation > 0:
                reason_bits.append("高於目標且超出 band，給出 SELL 建議")
            else:
                reason_bits.append("剛好在目標附近")

        if asset_type in ["VT", "TW"]:
            market_ticker = "VT" if asset_type == "VT" else "0050.TW"
            market_plan = market_deployments[market_ticker]
            if market_plan["invest"] > 0:
                status = "🔵 市場專屬抄底"
                action = f"依 {market_plan['level']} 建議投入 {market_plan['invest']:.1f}%"
                trigger = "TACTICAL_DEPLOYMENT"
                reason_bits.append(f"{market_plan['label']} 回檔 {market_plan['drawdown']:.2f}% 觸發專屬 Level")
            elif current_mode == "🔴 危機":
                status = "🔴 Crisis Override"
                action = "暫停一般再平衡；此標的目前無部署額"
                trigger = "HOLD"
                reason_bits.append("危機模式下只依此標的專屬 Level 部署")
        elif current_mode == "🔴 危機" and asset_type == "BOND":
            status = "🔴 Crisis Override"
            action = "暫停一般再平衡；依市場部署表管理防守資產"
            trigger = "HOLD"
            reason_bits.append("危機模式下債券由共用防守資產池規則管理")

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
    elif "🔵 市場專屬抄底" in rebalance_status_priority:
        st.info("🔵 VT／SPY／0050.TW 有專屬抄底訊號，請依上方市場部署表查看標的與額度。")
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
    st.dataframe(merrill_clock_reference_df, use_container_width=True, hide_index=True)

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
            "CNN Fear & Greed",
            "CNN 情緒確認層",
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
            f"{fg_classification['emoji']} {fg_score_input:.0f} ({fg_classification['label']})",
            fg_cross_summary,
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
            f"{fg_classification['emoji']} {fg_score_input:.0f} ({fg_classification['label']})｜CNN Fear & Greed 作為頂層情緒確認指標。建議：{fg_classification['action']}",
            f"{fg_cross_summary}｜CNN 情緒 vs VIX/CPI/Rate/Spread 交叉驗證結果。",
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

st.markdown(
    """
    <style>
    .tool-links {
        margin: 1.5rem 0 1rem;
        padding: 1rem 1.25rem;
        border: 1px solid rgba(120, 130, 150, .22);
        border-radius: 16px;
        background: linear-gradient(135deg, rgba(70, 110, 170, .08), rgba(110, 90, 170, .05));
    }
    .tool-links-title {
        margin: 0 0 .65rem;
        color: #526b91;
        font-size: .82rem;
        font-weight: 700;
        letter-spacing: .08em;
        text-transform: uppercase;
    }
    .tool-links-list {
        display: flex;
        flex-wrap: wrap;
        gap: .55rem;
    }
    .tool-links-list a {
        display: inline-flex;
        align-items: center;
        padding: .52rem .8rem;
        border: 1px solid rgba(90, 115, 155, .2);
        border-radius: 999px;
        background: rgba(255, 255, 255, .76);
        color: #526b91 !important;
        font-size: .92rem;
        font-weight: 600;
        text-decoration: none !important;
        transition: transform .15s ease, box-shadow .15s ease, background .15s ease;
    }
    .tool-links-list a:hover {
        transform: translateY(-2px);
        background: white;
        box-shadow: 0 5px 16px rgba(45, 65, 100, .12);
    }
    </style>
    <nav class="tool-links" aria-label="相關投資工具">
      <div class="tool-links-title">🔗 延伸工具與參考網站</div>
      <div class="tool-links-list">
        <a href="https://nedku.github.io/RetirementDynamicClaimRule/" target="_blank" rel="noopener noreferrer">GK 動態提領計算機 ↗</a>
        <a href="https://keepbuying.streamlit.app/" target="_blank" rel="noopener noreferrer">VM Timing 回測系統 ↗</a>
        <a href="https://nedku.github.io/Martin-Armstrong-ECM-Economic-Confidence-Model-/" target="_blank" rel="noopener noreferrer">Martin Armstrong ECM (V25.0 - Pure English) ↗</a>
        <a href="https://truthasset.streamlit.app/" target="_blank" rel="noopener noreferrer">Truth 1.0 Master Pro Dashboard ↗</a>
        <a href="https://economic-window.vercel.app/scorecard.html" target="_blank" rel="noopener noreferrer">滯脹版經濟櫥窗 v2 ↗</a>
        <a href="https://etfevaluation.streamlit.app/" target="_blank" rel="noopener noreferrer">ETF Dashboard ↗</a>
        <a href="https://perpbr7line.streamlit.app/" target="_blank" rel="noopener noreferrer">PER/PBR Trend ↗</a>
        <a href="https://ai.studio/apps/14786f1f-221c-49fd-843a-764b72ca9102" target="_blank" rel="noopener noreferrer">Rule#1估價 ↗</a>
        <a href="https://nedku.github.io/Macro-Risk-Scanner/" target="_blank" rel="noopener noreferrer">Risk Scanner ↗</a>
        <a href="https://technicalchart.streamlit.app/" target="_blank" rel="noopener noreferrer">TechnicalChart ↗</a>
      </div>
    </nav>
    """,
    unsafe_allow_html=True,
)
