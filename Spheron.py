import os
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo
 
# ==============================================================================
# 1. SET WORKING DIRECTORY
# ==============================================================================
# Work in the folder this script lives in (repo root on GitHub, anywhere locally)
target_folder = os.path.dirname(os.path.abspath(__file__))
os.chdir(target_folder)
print(f"Active working directory set to: {os.getcwd()}")
 
csv_file = 'spheron_pricing_history.csv'
today_date = datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d')
spheron_url = 'https://www.spheron.network/pricing/'
 
headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
}
 
 
# ==============================================================================
# 2. PARSER  (the page is a list of <button class="gpu-price-row">, not a <table>)
# ==============================================================================
def txt(el):
    """Visible text of an element with whitespace tidied (HTML comments are ignored)."""
    return " ".join(el.get_text().split()) if el else None
 
 
def num(s):
    """First number in a string, e.g. '$5.84' -> 5.84, '2768 GB RAM' -> 2768.0."""
    m = re.search(r'\d[\d,]*\.?\d*', s or '')
    return float(m.group().replace(',', '')) if m else None
 
 
def parse_spheron(html):
    soup = BeautifulSoup(html, 'html.parser')
    rows = soup.select('button.gpu-price-row')
    if not rows:
        raise RuntimeError("No GPU rows found - the page layout changed, or the prices are "
                           "loaded by JavaScript and not present in the raw HTML.")
 
    records = []
    for row in rows:
        # --- name / family / VRAM ---
        gpu = txt(row.find('h3'))
        family = txt(row.select_one('.gpu-price-family-inner'))
        vram = txt(row.select_one('.gpu-price-vram'))
 
        # --- specs column: RAM / vCPUs / NVMe, or a note like "Ships H2 2026" ---
        ram = vcpu = nvme = None
        note = None
        spec_div = row.find('div', class_='lg:w-[44%]')
        if spec_div:
            for s in spec_div.find_all('span'):
                t = txt(s)
                if not t:
                    continue
                if t.endswith('GB RAM'):
                    ram = num(t)
                elif t.endswith('vCPUs'):
                    vcpu = num(t)
                elif t.endswith('NVMe'):
                    nvme = num(t)
                else:
                    note = t
 
        # --- price column ---
        on_demand = spot = discount = None
        status = 'Priced'
        price_div = row.find('div', class_='lg:w-[24%]')
        main = price_div.find('div') if price_div else None
        if main is None:
            # No price shown, e.g. "Reserve" (not yet available)
            status = txt(price_div) or 'No price'
        else:
            price = num(txt(main.find('span', class_='font-ocr')))
            spot_only = main.find('span', title=lambda t: t and t.startswith('Spot price')) is not None
            if spot_only:
                spot = price                      # only a spot price is listed
                status = 'Spot only'
            else:
                on_demand = price
            badge = price_div.find('span', title=lambda t: t and t.startswith('Spot:'))
            if badge:
                m = re.search(r'Spot:\s*\$([\d.]+).*?(\d+)%\s*below', badge['title'])
                if m:
                    spot, discount = float(m.group(1)), float(m.group(2))
 
        records.append({
            'GPU': gpu,
            'Family': family,
            'VRAM_GB': num(vram),
            'RAM_GB': ram,
            'vCPUs': vcpu,
            'NVMe_GB': nvme,
            'OnDemandPriceUSD_perGPUhr': on_demand,
            'SpotPriceUSD_perGPUhr': spot,
            'SpotDiscountPct': discount,
            'Status': status,
            'Note': note,
        })
    return pd.DataFrame(records)
 
 
# ==============================================================================
# 3. SCRAPE AND SAVE DATA
# ==============================================================================
if __name__ == '__main__':
    already_scraped = False
    if os.path.exists(csv_file) and os.path.getsize(csv_file) > 0:
        existing_df = pd.read_csv(csv_file)
        if 'Date' in existing_df.columns and today_date in existing_df['Date'].astype(str).values:
            already_scraped = True
 
    if not already_scraped:
        print(f"New date detected ({today_date}). Scraping data...")
        page = requests.get(spheron_url, headers=headers, timeout=30)
        page.raise_for_status()
 
        # Diagnostics: shows in the GitHub log whether the real page was received
        n_rows = page.text.count('gpu-price-row')
        print(f"HTTP {page.status_code} | {len(page.text):,} chars | 'gpu-price-row' found {n_rows} times")
        if n_rows == 0:
            print("First 500 chars of the response (look for a bot/challenge page):")
            print(page.text[:500])
 
        new_df = parse_spheron(page.text)
        new_df.insert(0, 'Date', today_date)
 
        if not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0:
            new_df.to_csv(csv_file, index=False, mode='w')
            print(f"Created '{csv_file}' for {today_date} ({len(new_df)} rows).")
        else:
            new_df.to_csv(csv_file, index=False, mode='a', header=False)
            print(f"Appended {len(new_df)} rows for {today_date} to '{csv_file}'.")
    else:
        print(f"Data for today ({today_date}) already exists in '{csv_file}'. Skipping scrape.")
 
    spheron_df = [pd.read_csv(csv_file)]
    print(spheron_df[0].head(15).to_string())
