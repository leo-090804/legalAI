import json
import os
import re
import time
from datetime import datetime, timezone

from playwright.sync_api import Page, sync_playwright

# --- Configuration ---
BASE_DIR = r"d:\HW_Project\LegalAI"
INPUT_FILE = os.path.join(BASE_DIR, "data", "raw", "law_links.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "raw", "html")
REQUEST_DELAY_SECONDS = 2  # Delay between requests to be polite

USER_AGENT_STRING = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/108.0.0.0 Safari/537.36"
)
USER_AGENT = {"User-Agent": USER_AGENT_STRING}


# --- Helper Functions ---
def extract_law_id_from_url(url: str) -> str | None:
    """Extracts the ItemID from the URL to use as law_id."""
    match = re.search(r"ItemID=(\d+)", url)
    if match:
        return match.group(1)
    return None


def ensure_dir(directory: str):
    """Ensures that a directory exists, creating it if necessary."""
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")


def load_law_categories(input_file_path: str) -> list | None:
    """Loads law categories from the specified JSON file."""
    try:
        with open(input_file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Input file not found at {input_file_path}")
        return None
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {input_file_path}")
        return None


def fetch_and_save_law_details(
    page: Page, url: str, law_id: str, doc_type: str, output_dir: str
):
    """Fetches details for a single law URL, extracts info, and saves files."""
    html_file_path = os.path.join(output_dir, f"{law_id}.html")
    meta_file_path = os.path.join(output_dir, f"{law_id}_meta.json")

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    raw_html = page.content()

    # Extract title
    title = "Title not found"  # Default

    # 1. Try to extract from JavaScript variable 'title1'
    js_title_pattern = r"var title1\s*=\s*'(.*?)';"
    match_js_title = re.search(js_title_pattern, raw_html, re.IGNORECASE)
    if match_js_title:
        js_title_text = match_js_title.group(1).strip()
        if js_title_text:  # Check if not empty
            title = js_title_text

    # 2. If JS title not found or empty, try specific centered content title
    if title == "Title not found":
        content_title_selector_font = 'div.toanvancontent p[align="CENTER"] b font'
        content_title_el = page.query_selector(content_title_selector_font)
        if not content_title_el:  # Fallback if font tag is not present
            content_title_selector_b = 'div.toanvancontent p[align="CENTER"] b'
            content_title_el = page.query_selector(content_title_selector_b)

        if content_title_el:
            content_title_text = content_title_el.inner_text().strip()
            if content_title_text:
                title = content_title_text

    # 3. Fallback to breadcrumb if still not found or empty
    if title == "Title not found":
        breadcrumb_selector = "div.box-map ul li:last-of-type a"
        breadcrumb_title_element = page.query_selector(breadcrumb_selector)
        if breadcrumb_title_element:
            breadcrumb_text = breadcrumb_title_element.inner_text().strip()
            if breadcrumb_text:
                title = breadcrumb_text
                desc_selector = "div.toanvancontent > p.MsoNormal:nth-of-type(2) b font"
                desc_element = page.query_selector(desc_selector)
                if desc_element:
                    description = desc_element.inner_text().strip()
                    if description and description.lower() not in title.lower():
                        title = f"{title} {description}"

    fetch_timestamp_utc = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"

    metadata = {
        "law_id": law_id,
        "url": url,
        "title": title,
        "doc_type": doc_type,
        "fetch_timestamp_utc": fetch_timestamp_utc,
    }

    with open(html_file_path, "w", encoding="utf-8") as f_html:
        f_html.write(raw_html)
    print(f"Saved HTML to {html_file_path}")

    with open(meta_file_path, "w", encoding="utf-8") as f_meta:
        json.dump(metadata, f_meta, ensure_ascii=False, indent=4)
    print(f"Saved metadata to {meta_file_path}")


# --- Main Script ---
def main():
    ensure_dir(OUTPUT_DIR)

    law_categories = load_law_categories(INPUT_FILE)
    if not law_categories:
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT["User-Agent"])
        page = context.new_page()

        total_links_count = sum(
            len(category.get("links", [])) for category in law_categories
        )
        processed_links_count = 0
        attempted_fetches = 0
        skipped_count = 0

        print(f"Starting to process up to {total_links_count} links...")

        for category in law_categories:
            doc_type = category.get("loaivanban", "Unknown")
            links = category.get("links", [])

            for url in links:
                processed_links_count += 1
                print(
                    f"\n({processed_links_count}/{total_links_count}) "
                    f"Processing URL: {url}"
                )

                law_id = extract_law_id_from_url(url)
                if not law_id:
                    print(f"Could not extract law_id from URL: {url}. Skipping.")
                    continue

                html_file_path = os.path.join(OUTPUT_DIR, f"{law_id}.html")
                meta_file_path = os.path.join(OUTPUT_DIR, f"{law_id}_meta.json")

                if os.path.exists(html_file_path) and os.path.exists(meta_file_path):
                    print(
                        f"Files for law_id {law_id} (HTML and Meta) "
                        f"already exist. Skipping."
                    )
                    skipped_count += 1
                    continue

                attempted_fetches += 1
                try:
                    fetch_and_save_law_details(page, url, law_id, doc_type, OUTPUT_DIR)
                except Exception as e:
                    print(f"Error processing {url} (law_id: {law_id}): {e}")
                finally:
                    time.sleep(REQUEST_DELAY_SECONDS)

        context.close()
        browser.close()
        print("\n--- Processing Complete ---")
        print(f"Total links encountered: {processed_links_count}")
        print(f"Attempted fetches (new items): {attempted_fetches}")
        print(f"Skipped (already existed): {skipped_count}")


if __name__ == "__main__":
    main()
    # browser.close()
    # print("\n--- Processing Complete ---")
    # print(f"Total links encountered: {processed_links_count}")
    # print(f"Attempted fetches (new items): {attempted_fetches}")
    # print(f"Skipped (already existed): {skipped_count}")


if __name__ == "__main__":
    main()
