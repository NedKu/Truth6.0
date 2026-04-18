import requests
import re
import io
import pandas as pd
from datetime import datetime, timedelta
import yfinance as yf
import numpy as np

def test_full_fetch():
    end = datetime.now()
    start = end - timedelta(days=730)
    
    print("--- Testing FRED data fetch ---")
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
            print(f"Fetching {name} from {url}")
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text), index_col=0, parse_dates=True)
            dfs.append(df.rename(columns={code: name}))
        macro_df = pd.concat(dfs, axis=1, sort=False)
        print("FRED data fetch Successful.")
    except Exception as e:
        print(f"FRED data fetch FAILED: {e}")
        return

    print("\n--- Testing VIX data fetch ---")
    try:
        vix_df = yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=False)["Close"]
        print("VIX data fetch Successful.")
    except Exception as e:
        print(f"VIX data fetch FAILED: {e}")

    print("\n--- Testing PMI data fetch ---")
    pmi_info = None
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
            print(f"PMI match found: {pmi_match.group(0)}")
        else:
            print("PMI match NOT found (regex mismatch).")
    except Exception as e:
        print(f"PMI fetch FAILED: {e}")

    print("\n--- Testing Cleveland Fed data fetch ---")
    try:
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
            print("Cleveland Fed YoY table NOT found.")
            # Let's see if the caption changed
            captions = re.findall(r"<caption>(.*?)</caption>", nowcast_html, re.IGNORECASE | re.DOTALL)
            print(f"Available captions: {captions}")
            return

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
        
        print(f"Found {len(yoy_rows)} rows in YoY table.")
        for row in yoy_rows:
            print(row)

    except Exception as e:
        print(f"Cleveland Fed fetch FAILED: {e}")

if __name__ == "__main__":
    test_full_fetch()
