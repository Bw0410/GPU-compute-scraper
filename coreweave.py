# 1. Directory & Date Setup
target_folder = '/Users/brendonwong/Desktop/GPU power project'
os.makedirs(target_folder, exist_ok=True)
os.chdir(target_folder)

today_date = datetime.now().strftime('%Y-%m-%d')
na_csv = 'coreweave_na_pricing_history.csv'
eu_csv = 'coreweave_eu_pricing_history.csv'

# 2. Scrape CoreWeave
coreweave_url = 'https://www.coreweave.com/pricing'
coreweave_page = requests.get(coreweave_url)
soup = BeautifulSoup(coreweave_page.text, 'html.parser')

header_container = soup.find('div', class_='table-v2-header')
headers = [cell.text.strip() for cell in header_container.find_all('div', class_='table-v2-cell')]

data = []
for row in soup.find_all('div', class_='table-row-v2'):
    cells = row.find_all('div', class_='table-v2-cell')
    row_values = [" ".join(cell.text.split()) for cell in cells]
    data.append(row_values)

# 3. Insert Date at position 0
df = pd.DataFrame(data, columns=headers)
df.insert(0, 'Date', today_date)

# 4. Split into NA and Europe
north_america_coreweave_df = df.iloc[0:12].copy()
europe_coreweave_df = df.iloc[12:24].copy()

# 5. Check date & append function
def process_csv(df_region, filename):
    already_scraped = False
    if os.path.exists(filename):
        existing_df = pd.read_csv(filename)
        if 'Date' in existing_df.columns and today_date in existing_df['Date'].astype(str).values:
            already_scraped = True

    if not already_scraped:
        if not os.path.exists(filename):
            df_region.to_csv(filename, index=False, mode='w')
            print(f"Created '{filename}' for {today_date}.")
        else:
            df_region.to_csv(filename, index=False, mode='a', header=False)
            print(f"Appended records for {today_date} to '{filename}'.")
    else:
        print(f"Data for {today_date} already exists in '{filename}'. Skipping append.")

# Save both CSVs
process_csv(north_america_coreweave_df, na_csv)
process_csv(europe_coreweave_df, eu_csv)

# Display outputs
display(north_america_coreweave_df)
display(europe_coreweave_df)
