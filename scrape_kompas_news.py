"""
scrape_kompas_news.py
======================
Scraper berita Kompas.com menggunakan Playwright.
Mengekstrak artikel dari halaman utama dan berbagai kategori.

Kolom yang diekstrak per artikel:
  - judul, url, kategori, waktu, penulis, thumbnail, ringkasan

Output: hasil_scrape/kompas_news_<timestamp>.json
"""
import json
import time
import os
import sys
import logging
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─── Konfigurasi ─────────────────────────────────────────────────────────────
OUTPUT_DIR = "hasil_scrape"
TIMESTAMP  = int(time.time())
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"kompas_news_{TIMESTAMP}.json")

# Halaman/seksi yang akan discrape
SECTIONS = [
    {"name": "Utama",      "url": "https://www.kompas.com/"},
    {"name": "Nasional",   "url": "https://nasional.kompas.com/"},
    {"name": "Ekonomi",    "url": "https://money.kompas.com/"},
    {"name": "Teknologi",  "url": "https://tekno.kompas.com/"},
    {"name": "Olahraga",   "url": "https://bola.kompas.com/"},
    {"name": "Internasional", "url": "https://internasional.kompas.com/"},
    {"name": "Hiburan",    "url": "https://entertainment.kompas.com/"},
]

DELAY_BETWEEN_PAGES = 2  # detik

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("kompas_scraper")

# ─── Browser Helper ──────────────────────────────────────────────────────────

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

# ─── Ekstraksi Artikel via JavaScript di DOM ──────────────────────────────────

EXTRACT_SCRIPT = """
() => {
    const articles = [];
    const seen = new Set();

    // Selector untuk berbagai jenis card artikel Kompas
    const selectors = [
        'article',
        '.articleList-content',
        '.article--list',
        '.latest--content',
        '.mostPopular--content',
        '[class*="article"]',
        '[class*="card"]',
        '[class*="news"]',
        'div[data-type="article"]',
    ];

    // Kumpulkan semua elemen yang berpotensi jadi artikel
    const candidates = [];
    selectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => candidates.push(el));
    });

    // Juga ambil semua link yang mengandung pola URL artikel Kompas
    document.querySelectorAll('a[href]').forEach(a => {
        const href = a.href;
        // URL artikel Kompas biasanya: /read/ atau /-/xxxx.yyyy
        if (href && (href.includes('/read/') || href.match(/\\/[a-z0-9-]+\\.kompas\\.com\\/read\\//))) {
            if (!seen.has(href)) {
                seen.add(href);
                // Cari gambar dan waktu terdekat
                const card = a.closest('article, [class*="article"], [class*="card"], li, div') || a.parentElement;
                let img   = '';
                let time_ = '';
                let kelas = '';

                if (card) {
                    const imgEl  = card.querySelector('img');
                    const timeEl = card.querySelector('time, [class*="time"], [class*="date"]');
                    const catEl  = card.querySelector('[class*="categ"], [class*="rubrik"], [class*="label"]');
                    img   = imgEl  ? (imgEl.dataset.src || imgEl.src || '') : '';
                    time_ = timeEl ? (timeEl.getAttribute('datetime') || timeEl.innerText.trim()) : '';
                    kelas = catEl  ? catEl.innerText.trim() : '';
                }

                const judul = a.innerText.trim() || a.title || a.getAttribute('aria-label') || '';
                if (judul.length > 10) {
                    articles.push({
                        judul,
                        url: href,
                        kategori: kelas,
                        waktu: time_,
                        thumbnail: img.startsWith('http') ? img : ''
                    });
                }
            }
        }
    });

    return articles;
}
"""

def scrape_section(page, section: dict) -> list:
    """Scrape satu section/kategori Kompas."""
    url  = section["url"]
    name = section["name"]
    logger.info(f"Scraping [{name}]: {url}")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except PWTimeout:
        logger.warning(f"[{name}] Timeout saat load, mencoba lanjut...")

    # Tunggu artikel muncul
    try:
        page.wait_for_selector('article, [class*="article"], a[href*="/read/"]', timeout=8000)
    except PWTimeout:
        pass

    # Scroll untuk trigger lazy load
    page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
    page.wait_for_timeout(1200)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1000)

    articles = page.evaluate(EXTRACT_SCRIPT)

    # Tambahkan info section ke tiap artikel
    for art in articles:
        art["section"] = name

    logger.info(f"  ✓ [{name}] {len(articles)} artikel ditemukan")
    return articles


def deduplicate(articles: list) -> list:
    """Hapus duplikat berdasarkan URL."""
    seen = set()
    result = []
    for art in articles:
        if art["url"] not in seen:
            seen.add(art["url"])
            result.append(art)
    return result


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("\n" + "="*65)
    print("  📰 KOMPAS.COM NEWS SCRAPER")
    print("="*65 + "\n")

    all_articles = []

    with sync_playwright() as p:
        # Gunakan browser sistem jika ada (untuk bypass download yang lambat)
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
            viewport={"width": 1366, "height": 768},
            locale="id-ID"
        )
        page = context.new_page()

        # Blokir resource yang tidak perlu (iklan, tracking)
        page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,css}",
                   lambda r: r.abort() if any(x in r.request.url for x in
                   ["ads", "adserv", "doubleclick", "googlesynd", "chartbeat"]) else r.continue_())

        for i, section in enumerate(SECTIONS):
            articles = scrape_section(page, section)
            all_articles.extend(articles)
            if i < len(SECTIONS) - 1:
                time.sleep(DELAY_BETWEEN_PAGES)

        browser.close()

    # Deduplikasi
    unique = deduplicate(all_articles)
    logger.info(f"\nTotal artikel unik: {len(unique)} (dari {len(all_articles)} termasuk duplikat)")

    # Simpan output
    output = {
        "metadata": {
            "source": "kompas.com",
            "scrape_date": datetime.now().isoformat(),
            "timestamp": TIMESTAMP,
            "sections_scraped": [s["name"] for s in SECTIONS],
            "total_articles": len(unique)
        },
        "articles": unique
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    abs_path = os.path.abspath(OUTPUT_FILE)
    print("\n" + "="*65)
    print(f"  🎉 SELESAI! {len(unique)} ARTIKEL BERHASIL DIKUMPULKAN")
    print(f"  📂 File: {abs_path}")
    print("="*65 + "\n")

    # Preview per section
    from collections import Counter
    counts = Counter(art["section"] for art in unique)
    for sec, count in counts.items():
        print(f"  📌 {sec}: {count} artikel")

    # Preview 5 artikel pertama
    print("\n📰 5 Artikel Pertama:")
    for art in unique[:5]:
        print(f"  [{art['section']}] {art['judul'][:70]}")
        print(f"          {art['url']}")
        print()


if __name__ == "__main__":
    main()
