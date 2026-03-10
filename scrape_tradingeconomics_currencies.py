"""
scrape_tradingeconomics_currencies.py
======================================
Scraper Playwright untuk halaman mata uang TradingEconomics.
Data dirender oleh JavaScript — perlu browser headless untuk membaca tabel.

Kolom yang diekstrak:
  Nama, Harga, Hari, %, Mingguan, Bulanan, YTD, YoY, Tanggal

Output: hasil_scrape/tradingeconomics_currencies_<timestamp>.json
"""
import json
import time
import os
import sys
import logging
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─── Data Helpers ────────────────────────────────────────────────────────────

def get_browser_path():
    """Cari lokasi browser chromium di sistem sebagai fallback."""
    paths = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def safe_float(val: str):
    """Parse nilai numerik, fallback ke string asli."""
    if not val or val.strip() in ("-", "", "N/A"):
        return None
    cleaned = val.strip().replace(",", ".").replace("%", "").replace("+", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return val.strip()

def scrape_with_playwright():
    """Buka halaman dengan Playwright dan ekstrak tabel dari DOM yang sudah dirender."""
    all_data = {}
    total_pairs = 0

    with sync_playwright() as p:
        browser_path = get_browser_path()
        launch_args = {"headless": True}
        if browser_path:
            launch_args["executable_path"] = browser_path
            logger.info(f"Menggunakan browser sistem: {browser_path}")

        try:
            browser = p.chromium.launch(**launch_args)
        except Exception as e:
            logger.warning(f"Gagal launch browser: {e}. Mencoba tanpa executable_path...")
            browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="id-ID",
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        page.set_extra_http_headers({
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
            "Referer": "https://id.tradingeconomics.com/"
        })

        logger.info(f"Membuka halaman: {URL}")
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        except PWTimeout:
            logger.warning("Timeout pada domcontentloaded, mencoba lanjut...")

        # Tunggu tabel utama muncul
        try:
            page.wait_for_selector("table", timeout=15000)
            logger.info("Tabel ditemukan di DOM!")
        except PWTimeout:
            logger.warning("Tabel tidak ditemukan setelah 15 detik. Halaman mungkin memerlukan autentikasi.")

        # Ekstra tunggu agar semua baris ter-render
        page.wait_for_timeout(3000)

        # Scroll ke bawah untuk memastikan lazy-load ter-trigger
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1500)

        # Ekstrak semua tabel via JavaScript di dalam page context
        tables_data = page.evaluate("""
        () => {
            const results = [];
            const tables = document.querySelectorAll('table');
            
            tables.forEach((table, tableIdx) => {
                // Cari heading terdekat sebelum tabel
                let groupName = 'Grup ' + (tableIdx + 1);
                let prev = table.previousElementSibling;
                while (prev) {
                    const tag = prev.tagName.toLowerCase();
                    if (['h1','h2','h3','h4','h5'].includes(tag)) {
                        groupName = prev.innerText.trim();
                        break;
                    }
                    if (prev.tagName === 'TABLE') break;
                    prev = prev.previousElementSibling;
                }
                
                // Ambil headers
                const headerRow = table.querySelector('tr');
                if (!headerRow) return;
                const headers = Array.from(headerRow.querySelectorAll('th, td'))
                    .map(th => th.innerText.trim());
                if (headers.length < 3) return;
                
                // Ambil semua baris data
                const rows = [];
                const dataRows = Array.from(table.querySelectorAll('tr')).slice(1);
                
                dataRows.forEach(row => {
                    const cells = Array.from(row.querySelectorAll('td, th'));
                    if (cells.length < 3) return;
                    
                    const rowObj = {};
                    cells.forEach((cell, i) => {
                        if (i < headers.length) {
                            rowObj[headers[i]] = cell.innerText.trim();
                        }
                    });
                    if (Object.keys(rowObj).length > 0) rows.push(rowObj);
                });
                
                if (rows.length > 0) {
                    results.push({ group: groupName, headers, rows });
                }
            });
            
            return results;
        }
        """)

        logger.info(f"Berhasil mengekstrak {len(tables_data)} tabel dari DOM")

        # Proses setiap tabel
        NUMERIC_COLS = {"Harga", "Hari", "%", "Mingguan", "Bulanan", "YTD", "YoY",
                        "Last", "Day", "Weekly", "Monthly"}

        for tbl in tables_data:
            group_name = tbl["group"]
            headers = tbl["headers"]
            rows_raw = tbl["rows"]

            group_result = {}
            for row in rows_raw:
                # Kolom pertama (biasanya "Nama" atau "Currency") = symbol/name
                name_key = headers[0] if headers else "Nama"
                symbol = row.get(name_key, "").strip()
                if not symbol:
                    continue

                entry = {}
                for col, val in row.items():
                    if col == name_key:
                        continue
                    # Coba parse numerik untuk kolom harga/perubahan
                    if col in NUMERIC_COLS:
                        entry[col] = safe_float(val)
                    else:
                        entry[col] = val

                # Tentukan arah
                pct_val = entry.get("%") or entry.get("Day")
                if isinstance(pct_val, (int, float)):
                    entry["direction"] = "UP" if pct_val > 0 else ("DOWN" if pct_val < 0 else "FLAT")

                group_result[symbol] = entry
                total_pairs += 1

            if group_result:
                all_data[group_name] = group_result
                logger.info(f"  ✓ [{group_name}] {len(group_result)} pasangan")

        browser.close()

    return all_data, total_pairs

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\n" + "="*65)
    print("  🌐 TRADINGECONOMICS CURRENCY SCRAPER (Playwright)")
    print("="*65 + "\n")

    all_data, total_pairs = scrape_with_playwright()

    if not all_data:
        logger.error("Tidak ada data yang dapat diekstrak.")
        logger.error("Kemungkinan TradingEconomics memerlukan login/autentikasi.")
        return

    output = {
        "metadata": {
            "source": URL,
            "scrape_date": datetime.now().isoformat(),
            "timestamp": TIMESTAMP,
            "total_currency_pairs": total_pairs,
            "groups": list(all_data.keys())
        },
        "currencies": all_data
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=4)

    abs_path = os.path.abspath(OUTPUT_FILE)
    print("\n" + "="*65)
    print(f"  🎉 SELESAI! {total_pairs} PASANGAN MATA UANG BERHASIL DIEKSTRAK")
    print(f"  📂 File: {abs_path}")
    print("="*65 + "\n")

    # Preview data
    for group, pairs in all_data.items():
        print(f"\n📊 {group} ({len(pairs)} pasangan):")
        for i, (sym, d) in enumerate(pairs.items()):
            if i >= 5:
                print("  ...")
                break
            icon = "🟢" if d.get("direction") == "UP" else ("🔴" if d.get("direction") == "DOWN" else "⚪")
            price = d.get("Harga") or d.get("Last", "-")
            pct = d.get("%") or d.get("Day", "-")
            print(f"  {icon} {sym}: {price} | {pct}%")


if __name__ == "__main__":
    main()
