import streamlit as st
import pandas as pd
import requests
import io
import re
from datetime import datetime, timedelta
import yfinance as yf
from truthasset import fetch_macro_data

def debug_macro():
    print("Running fetch_macro_data from truthasset.py...")
    macro_df, vix_df, pmi_info, cpi_nowcast_info = fetch_macro_data()
    
    print(f"macro_df is None: {macro_df is None}")
    print(f"vix_df is None: {vix_df is None}")
    print(f"pmi_info is None: {pmi_info is None}")
    print(f"cpi_nowcast_info is None: {cpi_nowcast_info is None}")
    
    if cpi_nowcast_info:
        print("cpi_nowcast_info contents:")
        for k, v in cpi_nowcast_info.items():
            if k != 'cpi_yoy_series':
                print(f"  {k}: {v}")
    else:
        print("cpi_nowcast_info is None! This is why it shows N/A.")

if __name__ == "__main__":
    debug_macro()
