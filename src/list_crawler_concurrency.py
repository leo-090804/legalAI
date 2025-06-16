import concurrent.futures
import json
import logging
import math
import os
import re
from functools import partial
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

# URL
BASE_URL = "https://vbpl.vn"
START_PAGE_URL = "https://vbpl.vn/TW/Pages/vanban.aspx"

# CONCURRENCY SETTINGS
MAX_WORKERS = 8

# DATA DIR
DATA_DIR = "data/raw"
OUTPUT_JSON_FILE = os.path.join(DATA_DIR, "law_links.json")

# LOG DIR
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "failed_links.log")

# EXCLUDED STATUSES
EXCLUDED_STATUSES = ["Hết hiệu lực toàn bộ", "Chưa có hiệu lực"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/108.0.0.0 Safari/537.36"
    )
}


def setup_directories():
    """
    Make sure the data and log directories exist.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


setup_directories()

# LOGGING CONFIG
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8",
)


def extract_document_types(session):
    """
    Extracts the list of document types from the main page.
    """
    print("--- 1: Extracting document types ---")
    try:
        response = session.get(START_PAGE_URL, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        doc_type_list_ul = soup.select_one("ul#loaiVB")
        if not doc_type_list_ul:
            return None
        link_tags = doc_type_list_ul.select("li a")
        extracted_types = []
        for link_tag in link_tags:
            name = link_tag.get_text(strip=True)
            href = link_tag.get("href")
            if name and href:
                try:
                    parsed_url = urlparse(href)
                    query_params = parse_qs(parsed_url.query)
                    id_list = query_params.get("idLoaiVanBan")
                    if id_list:
                        extracted_types.append({"id": int(id_list[0]), "name": name})
                except (ValueError, IndexError):
                    continue
        print(f"-> Extract {len(extracted_types)} document types.")
        return extracted_types
    except requests.exceptions.RequestException as e:
        logging.critical(f"Could not extract document types: {e}")
        return None


def get_list_page_metadata(session, doc_type_id):
    """
    Fetch metadata for the list page of a specific document type.
    """
    params = {"idLoaiVanBan": doc_type_id, "dvid": 13, "Page": 1}
    try:
        response = session.get(START_PAGE_URL, params=params, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        items_on_page = soup.select("ul.listLaw > li")
        items_per_page = len(items_on_page)
        if items_per_page == 0:
            return {"last_page": 0, "items_per_page": 15}
        found_text_tag = soup.select_one("div.box-tab div.header ul li a.selected span")
        if found_text_tag:
            match = re.search(r"(\d[\d\.]*)\s*văn bản", found_text_tag.get_text())
            if match:
                total_items = int(match.group(1).replace(".", ""))
                last_page = math.ceil(total_items / items_per_page)
                return {"last_page": last_page, "items_per_page": items_per_page}
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching metadata for {doc_type_id}: {e}")
    return {"last_page": 1, "items_per_page": 15}


def fetch_and_parse_page(session, doc_type_id, page_num):
    """
    Fetches a specific page of document links for a given document type ID.
    """
    params = {"idLoaiVanBan": doc_type_id, "dvid": 13, "Page": page_num}
    links_on_page = set()
    try:
        response = session.get(START_PAGE_URL, params=params, timeout=45)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        law_items = soup.select("ul.listLaw > li")
        for item in law_items:
            status_tag = item.select_one("div.right p.red")
            if status_tag and any(
                excluded in status_tag.get_text(strip=True)
                for excluded in EXCLUDED_STATUSES
            ):
                continue

            link_tag = item.select_one("p.title > a")
            if link_tag and "href" in link_tag.attrs:
                full_url = urljoin(BASE_URL, link_tag["href"])
                links_on_page.add(full_url)
        return links_on_page
    except requests.exceptions.RequestException:
        return set()


def run_pipeline():
    session = requests.Session()
    session.headers.update(HEADERS)

    document_types_to_crawl = extract_document_types(session)
    if not document_types_to_crawl:
        print("Cannot proceed: No document types found or error during extraction.")
        return

    print("\n--- 2: Collecting links for each document type ---")
    all_results = []

    for doc_type in document_types_to_crawl:
        doc_id = doc_type["id"]
        doc_name = doc_type["name"]

        print(f"\n[Processing]: {doc_name} (ID: {doc_id})")

        page_meta = get_list_page_metadata(session, doc_id)
        last_page = page_meta["last_page"]

        if last_page == 0:
            print(f"-> No documents found for '{doc_name}'. Skipping.")
            all_results.append({"loaivanban": doc_name, "links": []})
            continue

        print(
            f"-> Found {last_page} pages. Starting crawl with {MAX_WORKERS} threads..."
        )

        valid_links_set = set()

        # ThreadPoolExecutor for concurrent fetching
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

            task = partial(fetch_and_parse_page, session, doc_id)
            pages_to_crawl = range(1, last_page + 1)
            results_iterator = executor.map(task, pages_to_crawl)

            for page_links in tqdm(
                results_iterator,
                total=last_page,
                desc=f"  Crawling '{doc_name}'",
                leave=False,
            ):
                valid_links_set.update(page_links)

        type_result = {"loaivanban": doc_name, "links": list(valid_links_set)}
        all_results.append(type_result)
        print(f"-> Completed '{doc_name}', found {len(valid_links_set)} valid links.")

    print("\n--- 3: Saving results ---")
    try:
        with open(OUTPUT_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=4)
        print(f"PROCESS COMPLETE! All results saved to file: {OUTPUT_JSON_FILE}")
    except IOError as e:
        print(f"Error writing file: {e}")
        logging.critical(f"Could not write output file {OUTPUT_JSON_FILE}: {e}")


if __name__ == "__main__":
    run_pipeline()
