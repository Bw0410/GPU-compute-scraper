
import time
import pandas as pd
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

URL = "https://cloud.google.com/products/compute/pricing/accelerator-optimized"
OUTPUT = "google_cloud_gpu_pricing.csv"

TABLE_SELECTOR = '[role="region"][aria-label="Pricing Table 1"] table'
DROPDOWN = '[role="combobox"]'
REGION_MENU = '[role="listbox"][aria-label="Region selector"]'

MAX_RETRIES = 3
UPDATE_WAIT_MS = 1500


def get_gpu_table(page):
    """Find the visible GPU pricing table by its headers."""

    tables = page.locator(TABLE_SELECTOR)

    for i in range(tables.count()):
        table = tables.nth(i)

        if not table.is_visible():
            continue

        headers = [
            h.strip()
            for h in table.locator("th").all_inner_texts()
        ]

        required = ["Machine type", "GPU", "Components"]

        if all(
            any(name in h for h in headers)
            for name in required
        ):
            return table

    raise RuntimeError("GPU pricing table not found")


def get_dropdown(page):
    """Find the visible region dropdown."""

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
    """Discover every region in the dropdown."""

    dropdown = get_dropdown(page)
    dropdown.click()

    options = page.locator(
        f'{REGION_MENU} [role="option"][data-value]'
    )

    options.first.wait_for(timeout=10000)

    regions = options.evaluate_all(
    """els => els.map(el => el.getAttribute("data-value"))"""
)

    page.keyboard.press("Escape")

    return list(dict.fromkeys(regions))


def get_current_region(page):
    """Read the currently selected region."""

    return get_dropdown(page).inner_text().strip()


def select_region(page, region):
    """
    Select a region using JavaScript to avoid
    Playwright click timeouts on dropdown options.
    """

    if get_current_region(page) == region:
        return

    dropdown = get_dropdown(page)

    if dropdown.get_attribute("aria-expanded") != "true":
        dropdown.click(timeout=5000)

    option = page.locator(
        f'{REGION_MENU} [role="option"][data-value]'
    ).filter(has_text=region).first

    option.wait_for(state="attached", timeout=10000)

    # Scroll option into view when possible
    option.evaluate("""
        el => el.scrollIntoView({
            block: "nearest",
            inline: "nearest"
        })
    """)

    # DOM click avoids Playwright's actionability checks
    option.evaluate("el => el.click()")

    # Confirm that the displayed region changed
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
    """Extract the original table values."""

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


def scrape_region(page, region):
    """Scrape a region with automatic retries."""

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            select_region(page, region)

            # Allow pricing values to update
            page.wait_for_timeout(UPDATE_WAIT_MS)

            records = scrape_table(page, region)

            if not records:
                raise RuntimeError("No table rows extracted")

            return records

        except Exception as e:
            print(
                f"  Attempt {attempt}/{MAX_RETRIES} "
                f"failed: {e}",
                flush=True
            )

            if attempt < MAX_RETRIES:
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)

    raise RuntimeError(
        f"Unable to scrape {region} after {MAX_RETRIES} attempts"
    )


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

        page.locator(TABLE_SELECTOR).first.wait_for(
            state="attached",
            timeout=30000
        )

        get_gpu_table(page)

        regions = get_regions(page)

        if not regions:
            raise RuntimeError("No regions discovered")

        print(f"Found {len(regions)} regions.", flush=True)

        for i, region in enumerate(regions, 1):
            start = time.monotonic()

            print(
                f"[{i}/{len(regions)}] Scraping {region}",
                flush=True
            )

            try:
                records = scrape_region(page, region)

                all_records.extend(records)

                # Save progress after every successful region
                df = pd.DataFrame(all_records)

                df.to_csv(
                    OUTPUT,
                    index=False,
                    encoding="utf-8-sig"
                )

                elapsed = time.monotonic() - start

                print(
                    f"  SUCCESS: {len(records)} rows "
                    f"({elapsed:.1f}s)",
                    flush=True
                )

            except Exception as e:
                failed_regions.append(region)

                print(
                    f"  FAILED: {region} - {e}",
                    flush=True
                )

        browser.close()

    if not all_records:
        raise RuntimeError("No pricing data extracted")

    df = pd.DataFrame(all_records)

    print("\n========== SCRAPING SUMMARY ==========")
    print(f"Total regions: {len(regions)}")
    print(f"Successful regions: {df['Region'].nunique()}")
    print(f"Failed regions: {len(failed_regions)}")
    print(f"Total rows: {len(df)}")
    print(f"Output: {OUTPUT}")

    if failed_regions:
        print("\nFailed regions:")
        for region in failed_regions:
            print(f" - {region}")

        raise RuntimeError(
            f"Scraping incomplete: {len(failed_regions)} regions failed"
        )

    print("\nAll regions scraped successfully!")


if __name__ == "__main__":
    main()
