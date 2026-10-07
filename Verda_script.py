from bs4 import BeautifulSoup 
import requests
import pandas as pd
from io import StringIO  # Required to fix the Pandas warning
from datetime import datetime, timezone
import os

# ==============================================================================
# 1. SET WORKING DIRECTORY (Insert this at the top of your script)
# ==============================================================================
target_folder = '/Users/brendonwong/Desktop/GPU power project'

# Create the folder if it doesn't already exist on your Mac
os.makedirs(target_folder, exist_ok=True)

# Change Python's active directory to your target folder
os.chdir(target_folder)
print(f"Active working directory set to: {os.getcwd()}")


# ==============================================================================
# 2. SCRAPE AND SAVE DATA
# ==============================================================================
csv_file = 'verda_pricing_history.csv'
today_date = datetime.now().strftime('%Y-%m-%d')
verda_url = 'https://verda.com/pricing'

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
    
    verda_page = requests.get(verda_url, headers=headers)
    verda_tables = pd.read_html(verda_page.text)
    
    new_df = verda_tables[0].copy()
    
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

# Load into your requested coreweave_df / verda_df format
verda_df = [pd.read_csv(csv_file)]
display(verda_df[0])