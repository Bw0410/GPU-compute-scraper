
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from datetime import datetime, timezone

URL = "https://cloud.google.com/products/compute/pricing/accelerator-optimized"

OUTPUT = "google_cloud_gpu_pricing.csv"

TABLE_SELECTOR = 'div[aria-label="Pricing Table 1"] table'


def get_regions(page):
    """Discover all available regions from the dropdown."""

    dropdown = page.get_by_role("combobox").first
    dropdown.click()

    page.get_by_role("listbox", name="Region selector").wait_for()

    regions = page.locator(
        '[role="listbox"][aria-label="Region selector"] '
        '[role="option"][data-value]'
    ).evaluate_all(
        "(elements) => elements.map(el => el.getAttribute('data-value'))"
    )

    # Close dropdown
    dropdown.click()

    return list(dict.fromkeys(regions))


def select_region(page, region):
    """Select a region and wait for the pricing table to update."""

    dropdown = page.get_by_role("combobox").first
    dropdown.click()

    page.get_by_role(
        "option",
        name=region,
        exact=True
    ).click()

    page.wait_for_timeout(1500)


def scrape_table(page, region):
    """Extract the table without changing its original values."""

    html = page.locator(TABLE_SELECTOR).first.inner_html()
    soup = BeautifulSoup(html, "lxml")

    table = soup.find("table")

    if table is None:
        # inner_html() returns the contents, not the table tag itself
        table = soup

    headers = [
        th.get_text(" ", strip=True)
        for th in table.select("th")
    ]

    rows = []

    for tr in table.select("tbody tr"):
        cells = [
            td.get_text(" ", strip=True)
            for td in tr.select("td")
        ]

        if len(cells) != len(headers):
            continue

        record = dict(zip(headers, cells))
        record = {
    "Date Scraped (UTC)": datetime.now(timezone.utc).isoformat(),
    "Region": region,
    **record
}

        rows.append(record)

    return rows


def main():
    all_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={"width": 1920, "height": 1080}
        )

        print("Opening Google Cloud pricing page...")

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=90000
        )

        page.locator(TABLE_SELECTOR).first.wait_for(
            timeout=60000
        )

        regions = get_regions(page)

        print(f"Found {len(regions)} regions.\n")

        for i, region in enumerate(regions, 1):
            try:
                print(f"[{i}/{len(regions)}] Scraping {region}")

                select_region(page, region)

                rows = scrape_table(page, region)

                all_rows.extend(rows)

                print(f"    Extracted {len(rows)} rows")

            except Exception as e:
                print(f"    ERROR: {e}")

        browser.close()

    if all_rows:
        df = pd.DataFrame(all_rows)

        df.to_csv(
            OUTPUT,
            index=False,
            encoding="utf-8-sig"
        )

        print("\nScraping complete!")
        print(f"Total rows: {len(df)}")
        print(f"Total regions: {df['Region'].nunique()}")
        print(f"Saved to: {OUTPUT}")

    else:
        print("No data extracted.")


if __name__ == "__main__":
    main()
