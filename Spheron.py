import os
import requests
import pandas as pd
from io import StringIO
from datetime import datetime
from zoneinfo import ZoneInfo

# ==============================================================================
# 1. SET WORKING DIRECTORY
# ==============================================================================
# Work in the folder this script lives in (repo root on GitHub, anywhere locally)
target_folder = os.path.dirname(os.path.abspath(__file__))
os.chdir(target_folder)
print(f"Active working directory set to: {os.getcwd()}")


# ==============================================================================
# 2. SCRAPE AND SAVE DATA
# ==============================================================================
csv_file = 'spheron_pricing_history.csv'
today_date = datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d')
spheron_url = 'https://www.spheron.network/pricing/'

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
}

# Check if today's date has already been recorded in the CSV
already_scraped = False

if os.path.exists(csv_file):
    existing_df = pd.read_csv(csv_file)
    if 'Date' in existing_df.columns:
        if today_date in existing_df['Date'].astype(str).values:
            already_scraped = True

# Scrape and save if it's a new date
if not already_scraped:
    print(f"New date detected ({today_date}). Scraping data...")
    
    spheron_page = requests.get(spheron_url, headers=headers, timeout=30)
    spheron_page.raise_for_status()
    spheron_tables = pd.read_html(StringIO(spheron_page.text))
    
    new_df = spheron_tables[0].copy()
    
    # Insert 'Date' at position 0 (far left column)
    new_df.insert(0, 'Date', today_date)

    if not os.path.exists(csv_file):
        # Create new CSV with header
        new_df.to_csv(csv_file, index=False, mode='w')
        print(f"Created '{csv_file}' inside '{target_folder}' for {today_date}.")
    else:
        # Append new data rows without repeating headers
        new_df.to_csv(csv_file, index=False, mode='a', header=False)
        print(f"Appended records for {today_date} to '{csv_file}'.")
else:
    print(f"Data for today ({today_date}) already exists in '{csv_file}'. Skipping scrape.")

# Load into your requested coreweave_df / spheron_df format
spheron_df = [pd.read_csv(csv_file)]
print(spheron_df[0].head())