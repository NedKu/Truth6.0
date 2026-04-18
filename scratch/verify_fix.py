import sys
import os
sys.path.append(os.getcwd())
from truthasset import fetch_macro_data

def verify():
    print("Fetching macro data...")
    m, v, p, c = fetch_macro_data()
    print("-" * 20)
    if c:
        print(f"Cleveland CPI Nowcast: {c.get('cpi_yoy_current')}% for {c.get('cpi_yoy_label')}")
        print(f"Cleveland Core PCE Nowcast: {c.get('pce_core_yoy_nowcast')}% for {c.get('pce_core_yoy_label')}")
        print(f"Updated at: {c.get('updated_at')}")
    else:
        print("Cleveland Fed data is STILL NULL!")
    
    if m is not None:
        print("FRED data: SUCCESS")
    else:
        print("FRED data: FAILED")
        
    if v is not None:
        print("VIX data: SUCCESS")
    else:
        print("VIX data: FAILED")

if __name__ == "__main__":
    verify()
