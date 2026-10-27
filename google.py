
import time
from datetime import datetime, timezone

import pandas as pd
from playwright.sync_api import sync_playwright

URL = (
    "https://cloud.google.com/products/compute/"
    "pricing/accelerator-optimized"
)

OUTPUT = "google_cloud_gpu_pricing.csv"

TABLE_SELECTOR = (
    '[role="region"][aria-label="Pricing Table 1"] table'
)

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


def get_region_dropdown(page):
    """
    Find the region dropdown belonging to the GPU table.
    """

    table = get_gpu_table(page)

    section = table.locator(
        "xpath=ancestor::div[contains(@class, 'UAquKe')][1]"
    )

    if section.count() == 0:
        raise RuntimeError("GPU pricing section not found")

    dropdowns = section.locator('[role="combobox"]')

    for i in range(dropdowns.count()):
        dropdown = dropdowns.nth(i)

        if dropdown.is_visible():
            return dropdown

    raise RuntimeError("GPU region dropdown not found")


def get_region_listbox(page, dropdown):
    """
    Locate the listbox linked to the dropdown.
    """

    listbox_id = dropdown.get_attribute("aria-controls")

    if not listbox_id:
        raise RuntimeError(
            "Region dropdown has no aria-controls attribute"
        )

    return page.locator(
        f'[role="listbox"][id="{listbox_id}"]'
    ).first


def get_current_region(dropdown):
    """Read the currently selected region."""

    return dropdown.locator(
        '[jsname="Fb0Bif"]'
    ).inner_text().strip()


def open_dropdown(dropdown):
    """Open the dropdown only if it is closed."""

    if dropdown.get_attribute("aria-expanded") != "true":
        dropdown.click(timeout=5000)


def get_regions(page):
    """Collect every region available in the dropdown."""

    dropdown = get_region_dropdown(page)

    open_dropdown(dropdown)

    listbox = get_region_listbox(page, dropdown)

    listbox.wait_for(
        state="visible",
        timeout=10000
    )

    regions = listbox.locator(
        '[role="option"][data-value]'
    ).evaluate_all("""
        elements => elements.map(
            el => el.getAttribute("data-value")
        )
    """)

    page.keyboard.press("Escape")

    regions = [
        region for region in regions if region
    ]

    return list(dict.fromkeys(regions))


def select_region(page, region):
    """
    Select a region using the dropdown's linked listbox.
    """

    dropdown = get_region_dropdown(page)

    current = get_current_region(dropdown)

    if current == region:
        return

    open_dropdown(dropdown)

    listbox = get_region_listbox(page, dropdown)

    # Match the exact data-value, not partial text.
    option = listbox.locator(
        '[role="option"][data-value]'
    ).filter(
        has_text=region
    ).first

    option.wait_for(
        state="attached",
        timeout=10000
    )

    # Scroll the option into the dropdown viewport.
    option.evaluate("""
        el => el.scrollIntoView({
            block: "nearest",
            inline: "nearest"
        })
    """)

    # Avoid Playwright actionability timeouts.
    option.evaluate("el => el.click()")

    # Verify that the selected region changed.
    page.wait_for_function(
        """({id, region}) => {
            const dropdown = Array.from(
                document.querySelectorAll(
                    '[role="combobox"]'
                )
            ).find(
                el => el.getAttribute("aria-controls") === id
            );

            if (!dropdown) return false;

            const label = dropdown.querySelector(
                '[jsname="Fb0Bif"]'
            );

            return label &&
                   label.textContent.trim() === region;
        }""",
        arg={
            "id": dropdown.get_attribute("aria-controls"),
            "region": region
        },
        timeout=10000
    )


def scrape_table(page, region):
    """
    Extract the original table values without
    changing the prices or column structure.
    """

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

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

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
    """
    Scrape one region, retrying failed selections.
    """

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            select_region(page, region)

            # Allow asynchronous pricing updates.
            page.wait_for_timeout(UPDATE_WAIT_MS)

            records = scrape_table(page, region)

            if not records:
                raise RuntimeError(
                    "No pricing rows extracted"
                )

            return records

        except Exception as e:
            print(
                f"  Attempt {attempt}/{MAX_RETRIES} "
                f"failed: {e}",
                flush=True
            )

            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

            if attempt < MAX_RETRIES:
                page.wait_for_timeout(1000)

    raise RuntimeError(
        f"Unable to scrape {region} after "
        f"{MAX_RETRIES} attempts"
    )


def main():

    all_records = []
    failed_regions = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1920,
                "height": 1080
            },
            locale="en-US"
        )

        page.set_default_timeout(10000)

        print(
            "Opening Google Cloud pricing page...",
            flush=True
        )

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.locator(
            TABLE_SELECTOR
        ).first.wait_for(
            state="attached",
            timeout=30000
        )

        get_gpu_table(page)

        regions = get_regions(page)

        if not regions:
            raise RuntimeError(
                "No regions discovered"
            )

        print(
            f"Found {len(regions)} regions.",
            flush=True
        )

        for i, region in enumerate(regions, 1):

            start = time.monotonic()

            print(
                f"[{i}/{len(regions)}] "
                f"Scraping {region}",
                flush=True
            )

            try:
                records = scrape_region(
                    page,
                    region
                )

                all_records.extend(records)

                # Save after every successful region.
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
        raise RuntimeError(
            "No pricing data extracted"
        )

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
            f"Scraping incomplete: "
            f"{len(failed_regions)} regions failed"
        )

    print("\nAll regions scraped successfully!")


if __name__ == "__main__":
    main()
