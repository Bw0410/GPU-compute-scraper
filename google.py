
import os
import time
import pandas as pd
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

URL = "https://cloud.google.com/products/compute/pricing/accelerator-optimized"
OUTPUT = "google_cloud_gpu_pricing.csv"

TABLE = '[role="region"][aria-label="Pricing Table 1"] table'
REGION_MENU = '[role="listbox"][aria-label="Region selector"]'
DROPDOWN = '[role="combobox"]'


def get_regions(page):
    page.locator(DROPDOWN).first.click()

    options = page.locator(
        f'{REGION_MENU} [role="option"][data-value]'
    )
    options.first.wait_for(timeout=10000)

    regions = options.evaluate_all(
        """els => els.map(el => el.getAttribute("data-value"))"""
    )

    return list(dict.fromkeys(regions))


def get_selected_region(page):
    return page.locator(
        f'{DROPDOWN} [jsname="Fb0Bif"]'
    ).first.inner_text().strip()


def select_region(page, region):
    dropdown = page.locator(DROPDOWN).first

    # Open only if the menu is closed
    if dropdown.get_attribute("aria-expanded") != "true":
        dropdown.click()

    page.locator(
        f'{REGION_MENU} [role="option"][data-value]'
    ).filter(has_text=region).first.click(timeout=10000)

    # Confirm the selector reflects the requested region
    page.wait_for_function(
        """region => {
            const el = document.querySelector(
                '[role="combobox"] [jsname="Fb0Bif"]'
            );
            return el && el.textContent.trim() === region;
        }""",
        arg=region,
        timeout=10000
    )


def scrape_table(page, region):
    data = page.locator(TABLE).evaluate("""
        table => {
            const clean = el => el.innerText.trim();

            const headers = Array.from(
                table.querySelectorAll("th")
            ).map(clean);

            const rows = Array.from(
                table.querySelectorAll("tbody tr")
            ).map(tr =>
                Array.from(tr.querySelectorAll("td")).map(clean)
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

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.locator(TABLE).wait_for(timeout=30000)

        regions = get_regions(page)
        print(f"Found {len(regions)} regions.", flush=True)

        for i, region in enumerate(regions, 1):
            start = time.monotonic()

            try:
                print(
                    f"[{i}/{len(regions)}] {region}",
                    flush=True
                )

                select_region(page, region)

                # Allow asynchronous pricing updates to settle.
                # This is a delay, not a guarantee of fresh prices.
                page.wait_for_timeout(1000)

                records = scrape_table(page, region)

                if not records:
                    raise ValueError("No rows extracted")

                all_records.extend(records)

                # Save progressively
                df = pd.DataFrame(all_records)
                df.to_csv(
                    OUTPUT,
                    index=False,
                    encoding="utf-8-sig"
                )

                elapsed = time.monotonic() - start
                print(
                    f"  Saved {len(records)} rows "
                    f"({elapsed:.1f}s)",
                    flush=True
                )

            except Exception as e:
                failed_regions.append(region)
                print(f"  ERROR: {e}", flush=True)

        browser.close()

    if not all_records:
        raise RuntimeError("No data was extracted.")

    print(f"\nSaved {len(all_records)} total rows.")
    print(f"Failed regions: {failed_regions}")

    # Fail the GitHub Action if coverage is incomplete
    if failed_regions:
        raise RuntimeError(
            f"Failed to scrape {len(failed_regions)} regions"
        )


if __name__ == "__main__":
    main()
