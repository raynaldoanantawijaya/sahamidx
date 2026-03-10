"""
scrape_pluang_stocks.py
=======================
Scraper khusus multi-page untuk Pluang US Stocks.
Mengekstrak semua data saham dari semua halaman (639 saham, 64 halaman)
menggunakan parsing Next.js __NEXT_DATA__ SSR tanpa memerlukan browser.

Output: hasil_scrape/pluang_all_stocks_<timestamp>.json
"""
import requests
import json
import re
import time
import os
import sys
import logging
from datetime import datetime

# --- Konfigurasi ---
BASE_URL = "https://pluang.com/explore/us-market/stocks"
TOTAL_PAGES = 64          # Total halaman Pluang (639 saham / 10 per page)
DELAY_SECONDS = 1.5       # Jeda antar request agar tidak terblokir
OUTPUT_DIR = "hasil_scrape"
TIMESTAMP = int(time.time())
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"pluang_all_stocks_{TIMESTAMP}.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://pluang.com/explore/us-market/stocks",
}

# --- Setup Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("pluang_stocks")

def extract_next_data(html: str) -> dict | None:
    """Mengekstrak __NEXT_DATA__ JSON yang disuntikkan Next.js ke dalam HTML."""
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            logger.warning(f"Gagal parse __NEXT_DATA__: {e}")
    return None

def parse_stocks_from_next_data(next_data: dict) -> dict:
    """Mengekstrak data saham dari struktur Next.js pageProps."""
    stocks = {}
    try:
        page_props = next_data.get("props", {}).get("pageProps", {})
        explore_data = page_props.get("data", {})
        asset_categories = explore_data.get("assetCategories", [])

        for cat in asset_categories:
            for sub_cat in cat.get("assetCategoryData", []):
                for asset in sub_cat.get("assets", []):
                    try:
                        tile = asset.get("tileInfo", {})
                        display = asset.get("display", {})
                        price_info = display.get("lastPriceAndPercentageChange", {})
                        cap_info = display.get("marketCap", {})

                        symbol = tile.get("symbol", "")
                        if not symbol:
                            continue

                        stocks[symbol] = {
                            "name": tile.get("name", ""),
                            "symbol": symbol,
                            "assetId": tile.get("assetId"),
                            "securityType": tile.get("securityType", ""),
                            "isTradable": tile.get("isTradable", False),
                            "currentPrice": price_info.get("currentPrice"),
                            "currentPriceDisplay": price_info.get("currentPriceDisplay", ""),
                            "percentageChange": round(price_info.get("percentageChange", 0), 4),
                            "percentageDisplay": price_info.get("percentageDisplay", ""),
                            "direction": price_info.get("arrowIcon", ""),
                            "lastClosingPrice": price_info.get("lastClosingPrice"),
                            "dividendAmount": price_info.get("dividendAmount", 0),
                            "marketCap": cap_info.get("value", ""),
                            "sparkLine": tile.get("sparkLine", "")
                        }
                    except Exception as e:
                        logger.debug(f"Gagal parse asset: {e}")
    except Exception as e:
        logger.warning(f"Gagal walk pageProps: {e}")
    return stocks

def scrape_page(page_num: int, session: requests.Session) -> dict:
    """Mengambil satu halaman dan mengembalikan dictionary saham."""
    url = f"{BASE_URL}?page={page_num}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        next_data = extract_next_data(resp.text)
        if next_data:
            stocks = parse_stocks_from_next_data(next_data)
            return stocks
        else:
            logger.warning(f"[Page {page_num}] __NEXT_DATA__ tidak ditemukan.")
            return {}
    except requests.RequestException as e:
        logger.error(f"[Page {page_num}] Request gagal: {e}")
        return {}

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = requests.Session()

    # Ambil total halaman dari page 1 terlebih dahulu
    logger.info("Mengambil halaman 1 untuk mendapatkan total halaman...")
    resp = session.get(f"{BASE_URL}?page=1", headers=HEADERS, timeout=15)
    next_data_p1 = extract_next_data(resp.text)
    total_pages = TOTAL_PAGES
    total_stocks = 639

    if next_data_p1:
        try:
            explore_data = next_data_p1["props"]["pageProps"]["data"]
            total_pages = explore_data.get("totalPageCount", TOTAL_PAGES)
            total_stocks = explore_data.get("totalCount", 639)
        except (KeyError, TypeError):
            pass

    logger.info(f"Total saham: {total_stocks} | Total halaman: {total_pages}")

    all_stocks = {}
    failed_pages = []

    print("\n" + "="*65)
    print(f"  🚀 MEMULAI SCRAPING {total_stocks} SAHAM DARI {total_pages} HALAMAN")
    print("="*65 + "\n")

    for page in range(1, total_pages + 1):
        logger.info(f"Scraping halaman {page}/{total_pages}...")
        stocks = scrape_page(page, session)

        if stocks:
            all_stocks.update(stocks)
            logger.info(f"  ✓ [Page {page}] +{len(stocks)} saham | Total: {len(all_stocks)}")
        else:
            failed_pages.append(page)
            logger.warning(f"  ✗ [Page {page}] Tidak ada data ditemukan.")

        # Jeda antar request
        if page < total_pages:
            time.sleep(DELAY_SECONDS)

    # Simpan hasil
    output = {
        "metadata": {
            "source": BASE_URL,
            "scrape_date": datetime.now().isoformat(),
            "timestamp": TIMESTAMP,
            "total_pages_scraped": total_pages,
            "total_stocks_found": len(all_stocks),
            "failed_pages": failed_pages
        },
        "stocks": all_stocks
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=4)

    abs_path = os.path.abspath(OUTPUT_FILE)

    print("\n" + "="*65)
    print(f"  🎉 SELESAI! {len(all_stocks)} SAHAM BERHASIL DIKUMPULKAN")
    if failed_pages:
        print(f"  ⚠ Halaman gagal: {failed_pages}")
    print(f"  📂 File disimpan di:")
    print(f"     {abs_path}")
    print("="*65 + "\n")

if __name__ == "__main__":
    main()
