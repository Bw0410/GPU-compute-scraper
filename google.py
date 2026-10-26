
import time
import pandas as pd
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

URL = "https://cloud.google.com/products/compute/pricing/accelerator-optimized"
OUTPUT = "google_cloud_gpu_pricing.csv"

TABLE_SELECTOR = '[role="region"][aria-label="Pricing Table 1"] table'
DROPDOWN = '[role="combobox"]'
REGION_MENU = '[role="listbox"][aria-label="Region selector"]'


def get_gpu_table(page):
    """Find the visible GPU pricing table using its headers."""

    tables = page.locator(TABLE_SELECTOR)

    for i in range(tables.count()):
        table = tables.nth(i)

        if not table.is_visible():
            continue

        headers = [
            h.strip()
            for h in table.locator("th").all_inner_texts()
        ]

        if all(
            any(name in h for h in headers)
            for name in ["Machine type", "GPU", "Components"]
        ):
            return table

    raise RuntimeError("GPU pricing table not found")


def get_dropdown(page):
    """Find the region selector associated with the GPU table."""

    dropdowns = page.locator(DROPDOWN)

    for i in range(dropdowns.count()):
        dropdown = dropdowns.nth(i)

        if not dropdown.is_visible():
            continue

        text = dropdown.inner_text().strip()

        if "(" in text and ")" in text:
            return dropdown

    raise RuntimeError("Region dropdown not found")


def get_regions(page):
    """Discover all available regions."""

    dropdown = get_dropdown(page)
    dropdown.click()

    options = page.locator(
        f'{REGION_MENU} [role="option"][data-value]'
    )

    options.first.wait_for(timeout=10000)

    regions = options.evaluate_all(
        """els => els.map(el => el.getAttribute("data-value"))"""
    )

    # Close the menu using Escape
    page.keyboard.press("Escape")

    return list(dict.fromkeys(regions))


def select_region(page, region):
    """Select a region from the dropdown."""

    dropdown = get_dropdown(page)

    if dropdown.get_attribute("aria-expanded") != "true":
        dropdown.click()

    option = page.locator(
        f'{REGION_MENU} [role="option"][data-value]'
    ).filter(has_text=region).first

    option.click(timeout=10000)

    # Confirm the dropdown displays the selected region
    page.wait_for_function(
        """region => {
            return Array.from(
                document.querySelectorAll('[role="combobox"]')
            ).some(el =>
                el.textContent.trim() === region
            );
        }""",
        arg=region,
        timeout=10000
    )


def scrape_table(page, region):
    """Extract all rows and columns without modifying values."""

    table = get_gpu_table(page)

    data = table.evaluate("""
        table => {
            const clean = el => el.innerText.trim();

            const headers = Array.from(
                table.querySelectorAll("th")
            ).map(clean);

            const rows = Array.from(
                table.querySelectorAll("tbody tr")
            ).map(tr =>
                Array.from(
                    tr.querySelectorAll("td")
                ).map(clean)
            );

            return {headers, rows};
        }
    """)

    timestamp = datetime.now(timezone.utc).isoformat()

    records = []

    for cells in data["rows"]:
        if len(cells) != len(data["headers"]):
            continue

        record = dict(zip(data["headers"], cells))

        records.append({
            "Date Scraped (UTC)": timestamp,
            "Region": region,
            **record
        })

    return records


def main():
    all_records = []
    failed_regions = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={"width": 1920, "height": 1080},
            locale="en-US"
        )

        page.set_default_timeout(10000)

        print("Opening Google Cloud pricing page...", flush=True)

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Wait for at least one matching table
        page.locator(TABLE_SELECTOR).first.wait_for(
            state="attached",
            timeout=30000
        )

        table = get_gpu_table(page)

        print(
            "GPU table found:",
            table.locator("th").all_inner_texts()[:3],
            flush=True
        )

        regions = get_regions(page)

        if not regions:
            raise RuntimeError("No regions discovered")

        print(f"Found {len(regions)} regions.", flush=True)

        for i, region in enumerate(regions, 1):
            start = time.monotonic()

            try:
                print(
                    f"[{i}/{len(regions)}] Scraping {region}",
                    flush=True
                )

                select_region(page, region)

                # Allow dynamic pricing data to update
                page.wait_for_timeout(1000)

                records = scrape_table(page, region)

                if not records:
                    raise ValueError("No rows extracted")

                all_records.extend(records)

                # Save CSV progressively
                df = pd.DataFrame(all_records)

                df.to_csv(
                    OUTPUT,
                    index=False,
                    encoding="utf-8-sig"
                )

                elapsed = time.monotonic() - start

                print(
                    f"  Saved {len(records)} rows "
                    f"in {elapsed:.1f}s",
                    flush=True
                )

            except Exception as e:
                failed_regions.append(region)
                print(f"  ERROR: {e}", flush=True)

        browser.close()

    if not all_records:
        raise RuntimeError("No pricing data extracted")

    df = pd.DataFrame(all_records)

    print("\nScraping complete!", flush=True)
    print(f"Total rows: {len(df)}", flush=True)
    print(f"Regions scraped: {df['Region'].nunique()}", flush=True)
    print(f"Failed regions: {failed_regions}", flush=True)
    print(f"Saved to: {OUTPUT}", flush=True)

    if failed_regions:
        raise RuntimeError(
            f"Failed to scrape {len(failed_regions)} regions: "
            + ", ".join(failed_regions)
        )


if __name__ == "__main__":
    main()

