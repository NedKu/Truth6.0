import requests
import re

def test_fetch():
    nowcast_page_url = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(nowcast_page_url, timeout=15, headers=headers)
        resp.raise_for_status()
        html = resp.text
        print(f"HTML length: {len(html)}")
        
        # Try to find the caption
        caption_pattern = r"<caption>\s*Inflation, year-over-year percent change\s*</caption>"
        match = re.search(caption_pattern, html, re.IGNORECASE)
        if match:
            print(f"Found caption match: {match.group(0)}")
        else:
            print("Caption NOT found using original regex.")
            # Search for any caption to see what they look like
            captions = re.findall(r"<caption>(.*?)</caption>", html, re.IGNORECASE | re.DOTALL)
            print(f"All captions found: {captions}")
            
        yoy_table_match = re.search(
            r"<caption>\s*Inflation, year-over-year percent change\s*</caption>.*?<tbody>(.*?)</tbody>",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        
        if yoy_table_match:
            print("Found table body.")
            tbody = yoy_table_match.group(1)
            row_matches = re.findall(r"<tr>(.*?)</tr>", tbody, re.IGNORECASE | re.DOTALL)
            print(f"Found {len(row_matches)} rows.")
            for i, row_html in enumerate(row_matches):
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.IGNORECASE | re.DOTALL)
                cleaned_cells = [re.sub(r"<.*?>", "", cell).replace("&nbsp;", " ").strip() for cell in cells]
                print(f"Row {i}: {cleaned_cells}")
        else:
            print("Table NOT found.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_fetch()
