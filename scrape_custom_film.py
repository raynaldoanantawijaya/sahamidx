#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  Universal Film/Drama Scraper (Menu 4: Link Lainnya)         ║
║  Teknik gabungan dari DrakorKita + ZeldaEternity + Azarug    ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  AUTO-DETECTION & TECHNIQUE SELECTION:                       ║
║                                                              ║
║  1. CRAWL LISTINGS (menemukan judul film):                   ║
║     • DrakorKita (/detail/ links) → batch 10 halaman paralel ║
║     • WP + Sitemap (sitemap_index.xml) → instan, paralel     ║
║     • WP + Pages (/page/N/) → sequential + archive fallback  ║
║     • Generic (?page=N) → sequential fallback                ║
║                                                              ║
║  2. SCRAPE DETAIL (metadata film):                           ║
║     • requests + BS4: structured selectors (GMR/WP themes)   ║
║     • DrakorKita fallback: .anf li, .gnr a, cast=, crew=     ║
║                                                              ║
║  3. VIDEO EXTRACTION:                                        ║
║     • AJAX: admin-ajax.php (GMR/WP themes)                   ║
║     • Static iframe: langsung dari HTML                      ║
║     • DrakorKita Series: Playwright klik .btn-svr per eps    ║
║     • DrakorKita Movie: Playwright grab iframe langsung      ║
║     • Fallback umum: Playwright extract_iframe_from_page()   ║
║                                                              ║
║  4. PARALLEL PROCESSING:                                     ║
║     • DrakorKita: 2 worker (Playwright = berat)              ║
║     • Non-DrakorKita: 5 worker (HTTP only = ringan)          ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import re
import json
import time
import logging
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ─── Setup ───────────────────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hasil_scrape")
os.makedirs(OUTPUT_DIR, exist_ok=True)

log = logging.getLogger("scrapers.custom")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# Domain iklan yang harus diabaikan pada iframe
AD_DOMAINS = [
    'dtscout.com', 'doubleclick', 'googlesyndication', 'adnxs.com',
    'popads', 'popcash', 'propeller', 'onclick', 'adsterra',
    'facebook.com', 'twitter.com', 'instagram.com', 'youtube.com',
    'klik.best',
]


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITAS UMUM
# ═══════════════════════════════════════════════════════════════════════════════

def _get_html(url: str, retries: int = 3) -> str | None:
    """Fetch HTML dengan retry progresif."""
    for attempt in range(retries):
        try:
            timeout = 15 + (attempt * 10)
            resp = SESSION.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                log.debug(f"Gagal fetch {url} setelah {retries}x: {e}")
    return None


def _is_ad_iframe(src: str) -> bool:
    """Deteksi apakah iframe src adalah iklan/sosmed."""
    if not src or src.startswith('about:'):
        return True
    src_lower = src.lower()
    return any(ad in src_lower for ad in AD_DOMAINS)


def _get_base_url(url: str) -> str:
    """Ekstrak base URL (scheme + domain)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _extract_post_id(soup) -> str:
    """Ekstrak WordPress post ID dari class body (postid-XXXXX) atau article."""
    body = soup.select_one("body")
    if body:
        for cls in body.get("class", []):
            if cls.startswith("postid-"):
                return cls.replace("postid-", "")
    article = soup.select_one("article")
    if article:
        art_id = article.get("id", "")
        m = re.search(r'post-(\d+)', art_id)
        if m:
            return m.group(1)
    # Fallback: cari di shortlink atau input hidden
    shortlink = soup.select_one('link[rel="shortlink"]')
    if shortlink:
        href = shortlink.get("href", "")
        m = re.search(r'\?p=(\d+)', href)
        if m:
            return m.group(1)
    return ""


def _title_clean(t: str) -> str:
    """Bersihkan judul dari prefix/suffix umum."""
    t = re.sub(r'\s+', ' ', t).strip()
    # Hapus prefix umum
    for prefix in ["Nonton ", "Download ", "Streaming ", "Permalink ke: "]:
        if t.startswith(prefix):
            t = t[len(prefix):]
    # Hapus suffix umum
    t = re.sub(r'\s*Subtitle Indonesia\s*$', '', t, flags=re.I)
    t = re.sub(r'\s*Sub Indo\s*$', '', t, flags=re.I)
    return t.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# FASE 1: LISTING CRAWLER — Temukan daftar film dari halaman beranda/kategori
# ═══════════════════════════════════════════════════════════════════════════════

# Selector prioritas untuk menemukan "kartu film" di berbagai theme WordPress
CARD_SELECTORS = [
    "article.item",                     # Azarug / LK21 / Rebahin theme
    ".gmr-box-content",                 # GMR theme
    "article",                          # ZeldaEternity / generic WP theme
    "a[href*='/detail/']",             # DrakorKita pattern (direct link cards)
    ".bsx",                             # Alternative WP streaming theme
    ".movie-item",                      # Movie theme
]

TITLE_SELECTORS = [
    ".entry-title a", "h2 a", "h3 a", ".title a", ".tt a", "h4 a",
]


def _fetch_listing_page(url: str, base_url: str) -> list[dict]:
    """Ekstrak daftar film dari satu halaman listing menggunakan structured selectors."""
    html = _get_html(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    # Coba setiap selector sampai menemukan kartu film
    cards = []
    used_selector = ""
    for selector in CARD_SELECTORS:
        cards = soup.select(selector)
        if cards:
            used_selector = selector
            break

    if not cards:
        log.debug(f"  Tidak ada kartu film ditemukan di {url}")
        return []

    log.debug(f"  Menggunakan selector '{used_selector}' — {len(cards)} kartu")

    for card in cards:
        # Cari link dan judul
        title = ""
        detail_url = ""

        # Metode 1: Cari dari title selectors di dalam kartu
        for ts in TITLE_SELECTORS:
            title_el = card.select_one(ts)
            if title_el:
                title = title_el.get_text(strip=True)
                detail_url = title_el.get("href", "")
                break

        # Metode 2: Jika kartu itu sendiri adalah <a> tag (DrakorKita style)
        if not detail_url and card.name == "a":
            detail_url = card.get("href", "")
            # Cari judul dari child elements
            for child_sel in [".title", "h3", "h4", ".name", ".tt"]:
                te = card.select_one(child_sel)
                if te:
                    title = te.get_text(strip=True)
                    break
            if not title:
                # Fallback: gunakan teks terpanjang yang bukan durasi
                for txt in card.stripped_strings:
                    if len(txt) > 5 and not re.match(r'^\d{1,2}:\d{2}', txt):
                        title = txt
                        break

        # Metode 3: Fallback ke <a> pertama di dalam kartu
        if not detail_url:
            first_a = card.select_one("a[href]")
            if first_a:
                detail_url = first_a.get("href", "")
                if not title:
                    title = first_a.get("title", "") or first_a.get_text(strip=True)

        if not detail_url or not title:
            continue

        # Normalisasi URL
        if not detail_url.startswith("http"):
            detail_url = urljoin(base_url, detail_url)

        # Filter: skip link yang bukan halaman film
        url_lower = detail_url.lower()
        skip_patterns = ['/genre/', '/category/', '/tag/', '/year/', '/country/',
                         '/page/', '/author/', 'javascript:', '#', 'mailto:',
                         'facebook.com', 'twitter.com', 'instagram.com']
        if any(p in url_lower for p in skip_patterns):
            continue

        # Bersihkan judul
        title = _title_clean(title)
        if len(title) < 3:
            continue

        # Poster
        poster = ""
        img = card.select_one("img")
        if img:
            poster = img.get("data-src") or img.get("src") or ""

        # Quality badge
        quality = ""
        q_el = card.select_one(".gmr-quality-item a, .gmr-quality-item, .quality")
        if q_el:
            quality = q_el.get_text(strip=True)

        # Rating
        rating = ""
        r_el = card.select_one(".gmr-rating-item, .rating")
        if r_el:
            rating = r_el.get_text(strip=True)

        items.append({
            "title": title,
            "detail_url": detail_url,
            "poster": poster,
            "quality": quality,
            "rating": rating,
        })

    return items


def _detect_next_page(soup, current_url: str, base_url: str) -> str | None:
    """Deteksi URL halaman selanjutnya dari pagination."""
    # Metode 1: CSS selector klasik
    next_btn = soup.select_one('a.next.page-numbers, a.next, a[rel="next"], .pagination .next a, .nav-next a')
    if next_btn:
        href = next_btn.get("href", "")
        if href and href != current_url:
            return href if href.startswith("http") else urljoin(base_url, href)

    # Metode 2: Cari berdasarkan teks "Next" atau "→"
    for a in soup.select('a[href]'):
        txt = a.get_text(strip=True).lower()
        if txt in ['next', 'next »', '»', '→', 'selanjutnya', 'berikutnya']:
            href = a.get("href", "")
            if href and href != current_url and 'javascript' not in href:
                return href if href.startswith("http") else urljoin(base_url, href)

    return None


def crawl_film_listings(start_url: str, max_pages: int = 9999, max_films: int = 99999) -> list[dict]:
    """Crawl daftar film dari halaman beranda/kategori TANPA BATAS sampai habis.
    DrakorKita mode: paralel 10 halaman sekaligus (10x lebih cepat).
    """
    base_url = _get_base_url(start_url)
    all_films = []
    seen_urls = set()
    current_url = start_url
    paging_mode = None  # 'wp' = /page/N/, 'query' = ?page=N, 'drakorkita' = /all?page=N

    # ── Auto-detect: DrakorKita sites menggunakan /all?page=N ──
    html_probe = _get_html(start_url)
    if html_probe:
        soup_probe = BeautifulSoup(html_probe, "html.parser")
        detail_links = soup_probe.select("a[href*='/detail/']")
        if detail_links:
            paging_mode = 'drakorkita'
            all_url = f"{base_url}/all"
            test_html = _get_html(all_url)
            if test_html:
                current_url = all_url
                log.info(f"  ℹ DrakorKita terdeteksi. Redirect ke: {all_url}")

    # ═══════════════════════════════════════════════════════════════════
    # FAST PATH: DrakorKita — paralel 10 halaman sekaligus
    # ═══════════════════════════════════════════════════════════════════
    if paging_mode == 'drakorkita':
        BATCH_SIZE = 10  # Fetch 10 halaman secara paralel

        def _fetch_page_batch(page_num):
            """Fetch satu halaman listing, return (page_num, films)."""
            url = f"{base_url}/all?page={page_num}"
            try:
                return (page_num, _fetch_listing_page(url, base_url))
            except Exception:
                return (page_num, [])

        page_num = 1
        consecutive_empty = 0

        while page_num <= max_pages:
            # Tentukan batch: page_num s/d page_num+BATCH_SIZE-1
            batch_start = page_num
            batch_end = min(page_num + BATCH_SIZE - 1, max_pages)
            batch_range = range(batch_start, batch_end + 1)

            log.info(f"  ⚡ Batch fetch halaman {batch_start}-{batch_end}...")

            # Paralel fetch
            batch_results = {}
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as ex:
                futures = {ex.submit(_fetch_page_batch, pn): pn for pn in batch_range}
                for f in as_completed(futures):
                    pn, films = f.result()
                    batch_results[pn] = films

            # Proses hasil secara berurutan (agar deduplicate konsisten)
            batch_total_new = 0
            for pn in sorted(batch_results.keys()):
                films = batch_results[pn]
                new_count = 0
                for film in films:
                    if film["detail_url"] not in seen_urls:
                        seen_urls.add(film["detail_url"])
                        all_films.append(film)
                        new_count += 1
                batch_total_new += new_count

                if not films or new_count == 0:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0

            log.info(f"     → {batch_total_new} judul baru (total: {len(all_films)})")

            if consecutive_empty >= 2:
                log.info(f"  ✓ Halaman kosong terdeteksi, semua film sudah terkumpul.")
                break

            page_num = batch_end + 1
            time.sleep(0.3)  # Jeda antar batch

        return all_films

    # ═══════════════════════════════════════════════════════════════════
    # NORMAL PATH: WP / Generic — sitemap first, lalu sequential pages
    # ═══════════════════════════════════════════════════════════════════

    # --- STEP 1: Coba sitemap dulu (instan, < 5 detik) ---
    sitemap_found = False
    try:
        sitemap_index_url = f"{base_url}/sitemap_index.xml"
        sitemap_xml = _get_html(sitemap_index_url)
        if sitemap_xml and '<sitemap>' in sitemap_xml:
            sm_matches = re.findall(r'<loc>(.*?post-sitemap\d*\.xml)</loc>', sitemap_xml)
            if sm_matches:
                log.info(f"  ⚡ Sitemap ditemukan: {len(sm_matches)} post-sitemap files (mode cepat)")

                def _parse_sitemap(sm_url):
                    urls = []
                    try:
                        xml = _get_html(sm_url)
                        if xml:
                            locs = re.findall(r'<loc>(.*?)</loc>', xml)
                            for loc in locs:
                                loc = loc.strip()
                                if loc == base_url or loc == f"{base_url}/":
                                    continue
                                if any(skip in loc for skip in ['/category/', '/genre/', '/tag/',
                                                                 '/author/', '/page/', '/wp-',
                                                                 '/sitemap', '/feed/', '/comment']):
                                    continue
                                urls.append(loc)
                    except Exception:
                        pass
                    return urls

                with ThreadPoolExecutor(max_workers=5) as ex:
                    futures = {ex.submit(_parse_sitemap, sm): sm for sm in sm_matches}
                    for f in as_completed(futures):
                        for url in f.result():
                            if url not in seen_urls:
                                seen_urls.add(url)
                                slug = url.rstrip('/').split('/')[-1]
                                title = slug.replace('-', ' ').title()
                                all_films.append({
                                    "title": title,
                                    "detail_url": url,
                                    "poster": "",
                                })

                if len(all_films) > 50:
                    sitemap_found = True
                    log.info(f"     → {len(all_films)} judul dari sitemap")
    except Exception:
        pass

    # --- STEP 2: Jika sitemap tidak cukup, crawl halaman sequential ---
    if not sitemap_found:
        # Ambil film dari halaman pertama (sudah di-probe di atas)
        if html_probe:
            films_p1 = _fetch_listing_page(start_url, base_url)
            for f in films_p1:
                if f["detail_url"] not in seen_urls:
                    seen_urls.add(f["detail_url"])
                    all_films.append(f)

        consecutive_empty = 0

        for page_num in range(2, max_pages + 1):
            if paging_mode == 'query':
                from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
                parsed = urlparse(current_url)
                params = parse_qs(parsed.query)
                params['page'] = [str(page_num)]
                current_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))
            elif paging_mode == 'wp':
                m = re.search(r'/page/(\d+)/', current_url)
                if m:
                    current_url = re.sub(r'/page/\d+/', f'/page/{page_num}/', current_url)
                else:
                    current_url = f"{start_url.rstrip('/')}/page/{page_num}/"
            else:
                # Auto-detect paging mode
                next_try = f"{start_url.rstrip('/')}/page/{page_num}/"
                test_html = _get_html(next_try)
                if test_html:
                    test_films = _fetch_listing_page(next_try, base_url)
                    if test_films:
                        current_url = next_try
                        paging_mode = 'wp'
                    else:
                        # Coba ?page=N
                        next_try = f"{start_url.rstrip('/')}?page={page_num}" if '?' not in start_url else start_url + f"&page={page_num}"
                        test_html = _get_html(next_try)
                        if test_html:
                            test_films = _fetch_listing_page(next_try, base_url)
                            if test_films:
                                current_url = next_try
                                paging_mode = 'query'
                            else:
                                break
                        else:
                            break
                else:
                    break

            log.info(f"  📄 Halaman {page_num}: {current_url}")

            films = _fetch_listing_page(current_url, base_url)

            new_count = 0
            for f in films:
                if f["detail_url"] not in seen_urls:
                    seen_urls.add(f["detail_url"])
                    all_films.append(f)
                    new_count += 1

            log.info(f"     → {new_count} judul baru (total: {len(all_films)})")

            if not films or new_count == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    log.info(f"  ✓ 2 halaman kosong berturut-turut, selesai.")
                    break
            else:
                consecutive_empty = 0

            time.sleep(0.5)

        # Archive fallback jika masih sedikit
        if len(all_films) < 200:
            log.info(f"  ℹ Hanya {len(all_films)} film. Mencari archive...")
            for archive_slug in ['best-rating', 'popular', 'movies', 'film']:
                archive_url = f"{base_url}/{archive_slug}/"
                test_html = _get_html(archive_url)
                if test_html:
                    test_films = _fetch_listing_page(archive_url, base_url)
                    if test_films:
                        log.info(f"  📄 Archive /{archive_slug}/ ditemukan. Crawling...")
                        for f in test_films:
                            if f["detail_url"] not in seen_urls:
                                seen_urls.add(f["detail_url"])
                                all_films.append(f)
                        arch_page = 1
                        arch_empty = 0
                        while arch_page <= 500:
                            arch_page += 1
                            arch_url = f"{base_url}/{archive_slug}/page/{arch_page}/"
                            arch_films = _fetch_listing_page(arch_url, base_url)
                            nc = 0
                            for f in arch_films:
                                if f["detail_url"] not in seen_urls:
                                    seen_urls.add(f["detail_url"])
                                    all_films.append(f)
                                    nc += 1
                            if nc == 0:
                                arch_empty += 1
                                if arch_empty >= 2:
                                    break
                            else:
                                arch_empty = 0
                            if arch_page % 10 == 0:
                                log.info(f"     → Halaman {arch_page}: total {len(all_films)} judul")
                            time.sleep(0.3)
                        log.info(f"     → Archive /{archive_slug}/: total {len(all_films)} judul")
                        break

    return all_films


# ═══════════════════════════════════════════════════════════════════════════════
# FASE 2: DETAIL SCRAPER — Scrape metadata, episodes, downloads dari 1 film
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_video_embeds_via_ajax(post_id: str, page_url: str, base_url: str) -> list[dict]:
    """Ambil video embed URLs via WordPress AJAX endpoint (teknik ZeldaEternity)."""
    embeds = []
    if not post_id:
        return embeds

    ajax_url = f"{base_url}/wp-admin/admin-ajax.php"

    for tab in ["p1", "p2", "p3", "p4"]:
        try:
            resp = SESSION.post(ajax_url, data={
                "action": "muvipro_player_content",
                "tab": tab,
                "post_id": post_id,
            }, headers={
                **HEADERS,
                "Referer": page_url,
                "X-Requested-With": "XMLHttpRequest",
            }, timeout=10)

            if resp.status_code == 200 and resp.text.strip():
                frag = BeautifulSoup(resp.text, "html.parser")
                iframe = frag.select_one("iframe")
                if iframe:
                    src = iframe.get("src") or iframe.get("SRC") or ""
                    if src and not _is_ad_iframe(src):
                        embeds.append({"server": tab, "url": src})
        except Exception:
            pass

    return embeds


def scrape_detail(detail_url: str, base_url: str) -> dict:
    """Scrape halaman detail film/series: metadata, episodes, downloads, video."""
    result = {
        "title": "",
        "detail_url": detail_url,
        "type": "Movie",
        "poster": "",
        "sinopsis": "",
        "genres": [],
        "cast": [],
        "directors": [],
        "year": "",
        "country": "",
        "rating": "",
        "quality": "",
        "duration": "",
        "episodes": [],
        "total_episodes": 0,
        "download_links": [],
        "video_embed": "",
        "video_servers": [],
    }

    html = _get_html(detail_url, retries=5)
    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")

    # ── JUDUL ──
    # Prioritas: h1[itemprop=headline] > h1.entry-title > 2nd h1 (DrakorKita: 1st h1 = site title)
    h1_headline = soup.select_one('h1[itemprop="headline"]')
    h1_entry = soup.select_one('h1.entry-title')
    all_h1 = soup.select('h1')

    if h1_headline:
        result["title"] = _title_clean(h1_headline.get_text(strip=True))
    elif h1_entry:
        result["title"] = _title_clean(h1_entry.get_text(strip=True))
    elif len(all_h1) >= 2:
        # DrakorKita: h1 pertama = "Drama Korea" (site title), h1 kedua = judul film
        result["title"] = _title_clean(all_h1[1].get_text(strip=True))
    elif all_h1:
        result["title"] = _title_clean(all_h1[0].get_text(strip=True))

    # Fallback dari slug
    if not result["title"] or result["title"].lower() in ['drama korea', 'nonton', 'streaming']:
        slug = detail_url.rstrip("/").split("/")[-1]
        parts = slug.split("-")
        if len(parts) >= 2 and len(parts[-1]) <= 5 and parts[-1].isalnum():
            parts = parts[:-1]
        result["title"] = " ".join(parts).title()

    # ── POSTER ──
    for sel in ["img.wp-post-image", ".thumb img", ".gmr-movie-data img",
                 ".poster img", "img[itemprop='image']"]:
        img = soup.select_one(sel)
        if img:
            result["poster"] = img.get("data-src") or img.get("src") or ""
            break

    # ── SINOPSIS ──
    content = soup.select_one(".entry-content, .desc, .sinopsis")
    if content:
        paragraphs = content.select("p")
        valid = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30]
        result["sinopsis"] = "\n".join(valid[:3])

    if not result["sinopsis"]:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            result["sinopsis"] = meta_desc.get("content", "")

    # ── METADATA (.gmr-moviedata / .gmr-movie-meta / .infox .spe / .anf li) ──
    meta_selectors = [".gmr-moviedata", ".gmr-movie-meta", ".gmr-movie-meta-list .gmr-movie-meta",
                      ".infox .spe span", ".anf li"]
    for meta_sel in meta_selectors:
        for meta_div in soup.select(meta_sel):
            label_full = meta_div.get_text(" ", strip=True).lower()

            if "genre" in label_full and not result["genres"]:
                result["genres"] = [a.get_text(strip=True) for a in meta_div.select("a")] or \
                                   [g.strip() for g in label_full.split(":")[-1].split(",") if g.strip()]
            elif any(k in label_full for k in ["pemain", "cast", "bintang", "aktor"]) and not result["cast"]:
                result["cast"] = [a.get_text(strip=True) for a in meta_div.select("a")] or \
                                 [c.strip() for c in label_full.split(":")[-1].split(",") if c.strip()]
            elif any(k in label_full for k in ["sutradara", "director", "direksi"]) and not result["directors"]:
                result["directors"] = [a.get_text(strip=True) for a in meta_div.select("a")] or \
                                      [d.strip() for d in label_full.split(":")[-1].split(",") if d.strip()]
            elif any(k in label_full for k in ["rilis", "tahun", "year", "release"]) and not result["year"]:
                links = meta_div.select("a")
                if links:
                    result["year"] = links[0].get_text(strip=True)
                else:
                    m = re.search(r'(\d{4})', label_full)
                    if m:
                        result["year"] = m.group(1)
            elif any(k in label_full for k in ["negara", "country"]) and not result["country"]:
                result["country"] = ", ".join(a.get_text(strip=True) for a in meta_div.select("a"))
            elif any(k in label_full for k in ["rating", "imdb"]) and not result["rating"]:
                m = re.search(r'[\d.]+', label_full)
                if m:
                    result["rating"] = m.group(0)
            elif any(k in label_full for k in ["kualitas", "quality"]) and not result["quality"]:
                links = meta_div.select("a")
                result["quality"] = links[0].get_text(strip=True) if links else ""
            elif any(k in label_full for k in ["durasi", "duration"]) and not result["duration"]:
                m = re.search(r'[\d]+\s*(?:min|menit|jam)', label_full, re.IGNORECASE)
                result["duration"] = m.group(0) if m else ""

    # ── FALLBACK METADATA: DrakorKita ".anf li" dengan " : " separator ──
    if not result["genres"] or not result["cast"]:
        info_fields = {}
        for li in soup.select(".anf li"):
            text = li.get_text(" ", strip=True)
            if " : " in text:
                key, _, value = text.partition(" : ")
                info_fields[key.strip().lower()] = value.strip()
        # Juga cek standalone <span> dengan " : "
        if not info_fields:
            for span in soup.select("span"):
                text = span.get_text(" ", strip=True)
                if " : " in text and len(text) < 150:
                    key, _, value = text.partition(" : ")
                    k = key.strip().lower()
                    if k and k not in ("sinopsis", "informasi") and k not in info_fields:
                        info_fields[k] = value.strip()

        if not result["year"] and info_fields.get("first_air_date"):
            result["year"] = info_fields["first_air_date"]
        if not result["type"]:
            result["type"] = info_fields.get("type", "Movie")
        result["status"] = info_fields.get("status", "")
        result["season"] = info_fields.get("season", "")

    # ── FALLBACK GENRE: DrakorKita (.gnr a) ──
    if not result["genres"]:
        gnr = soup.select_one(".gnr")
        if gnr:
            result["genres"] = [a.get_text(strip=True) for a in gnr.select("a") if a.get_text(strip=True)]
        else:
            # URL-param based genre links
            infox = soup.select_one(".infox, .detail-content")
            search_area = infox if infox else soup
            result["genres"] = [a.get_text(strip=True) for a in search_area.select("a[href*='genre=']") if a.get_text(strip=True)]

    # ── FALLBACK CAST: DrakorKita (a[href*='cast=']) ──
    if not result["cast"]:
        cast_area = soup.select_one(".desc-wrap") or soup.select_one(".infox") or soup
        raw_cast = [a.get_text(strip=True) for a in cast_area.select("a[href*='cast=']") if a.get_text(strip=True)]
        # Fix DrakorKita merged "Lee Na-youngas Yoon Ra-young" → "Lee Na-young"
        # Pattern: actor name ends with lowercase, then "as" merges, then role starts with uppercase
        cleaned = []
        for c in raw_cast:
            # Find pattern: lowercase letter + "as" + uppercase letter → split and keep left part
            m = re.search(r'^(.+?[a-z])as\s*[A-Z]', c)
            if m:
                c = m.group(1)
            c = c.strip()
            if c and c not in cleaned:
                cleaned.append(c)
        result["cast"] = cleaned

    # ── FALLBACK DIRECTORS: DrakorKita (a[href*='crew=']) ──
    if not result["directors"]:
        crew_area = soup.select_one(".desc-wrap") or soup.select_one(".infox") or soup
        result["directors"] = [a.get_text(strip=True) for a in crew_area.select("a[href*='crew=']") if a.get_text(strip=True)]

    # ── EPISODE LIST (Static HTML) ──
    ep_selectors = [
        ".gmr-listseries a",
        ".episodelist a",
        "ul.lstep li a",
        ".list-episode li a",
        ".eplister li a",
    ]
    episodes = []
    seen_ep = set()
    for ep_sel in ep_selectors:
        for ep_link in soup.select(ep_sel):
            ep_url = ep_link.get("href", "")
            ep_text = ep_link.get_text(strip=True)
            if ep_url and ep_url not in seen_ep and ep_text:
                if not ep_url.startswith("http"):
                    ep_url = urljoin(base_url, ep_url)
                seen_ep.add(ep_url)
                episodes.append({"label": ep_text, "url": ep_url})

    if episodes:
        result["type"] = "TV Series"
        result["total_episodes"] = len(episodes)
        result["episodes"] = episodes

    # ── FALLBACK EPISODE COUNT: Parse dari judul (DrakorKita: "Episode 1 - 8") ──
    if not episodes:
        ep_count = 0
        # Cari dari judul: "Episode 1 - 8" → 8
        title_text = result.get("title", "")
        ep_match = re.search(r'Episode\s+\d+\s*[-~]\s*(\d+)', title_text, re.I)
        if ep_match:
            ep_count = int(ep_match.group(1))
        # Fallback: cari dari metadata info_fields
        if not ep_count:
            ep_count_str = result.get("episode_count", "") or ""
            try:
                ep_count = int(re.search(r'\d+', ep_count_str).group()) if ep_count_str else 0
            except (AttributeError, ValueError):
                pass

        if ep_count > 0:
            result["type"] = "TV Series"
            result["total_episodes"] = ep_count
            # Generate placeholder episodes (akan diisi Playwright di _process_film)
            result["episodes"] = [{"label": f"Episode {i}", "url": ""} for i in range(1, ep_count + 1)]
            result["_needs_playwright_episodes"] = True  # Flag untuk pipeline

    # ── DOWNLOAD LINKS ──
    downloads = []
    dl_area = soup.select_one("#download, .download, .gmr-download-list, .soraddlx")
    if dl_area:
        for a in dl_area.select("a[href]"):
            href, text = a.get("href", ""), a.get_text(strip=True)
            if href and "javascript" not in href.lower() and "klik.best" not in href:
                downloads.append({"text": text or "Download", "url": href})
    else:
        # DrakorKita: tombol #nonot
        dl_btn = soup.select_one("#nonot, a[id='nonot']")
        if dl_btn:
            dl_url = dl_btn.get("href", "")
            if dl_url and dl_url != "#":
                downloads.append({"text": "DOWNLOAD", "url": dl_url})
        else:
            # Fallback: cari tombol download
            for a in soup.select("a[href]"):
                txt = a.get_text(strip=True).lower()
                if "download" in txt or "unduh" in txt:
                    href = a.get("href", "")
                    if href and "javascript" not in href.lower():
                        downloads.append({"text": a.get_text(strip=True), "url": href})

    result["download_links"] = downloads

    # ── VIDEO EMBED via AJAX (cepat, tanpa browser) ──
    post_id = _extract_post_id(soup)
    if post_id:
        embeds = _fetch_video_embeds_via_ajax(post_id, detail_url, base_url)
        if embeds:
            result["video_embed"] = embeds[0]["url"]
            result["video_servers"] = embeds

    # ── FALLBACK: Jika AJAX gagal, cek iframe statis ──
    if not result["video_embed"]:
        iframe = soup.select_one("iframe[src]")
        if iframe:
            src = iframe.get("src", "")
            if not _is_ad_iframe(src):
                result["video_embed"] = src

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# FASE 3: EPISODE SCRAPER — Scrape video embed per episode DENGAN VERIFIKASI
# ═══════════════════════════════════════════════════════════════════════════════

def _scrape_episode_video(ep_url: str, base_url: str) -> dict:
    """Scrape video embed dari halaman episode tunggal."""
    result = {"url": ep_url, "video_embed": "", "video_servers": [], "download_links": []}

    html = _get_html(ep_url, retries=3)
    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")

    # AJAX dulu (paling cepat)
    post_id = _extract_post_id(soup)
    if post_id:
        embeds = _fetch_video_embeds_via_ajax(post_id, ep_url, base_url)
        if embeds:
            result["video_embed"] = embeds[0]["url"]
            result["video_servers"] = embeds

    # Fallback iframe statis
    if not result["video_embed"]:
        iframe = soup.select_one("iframe[src]")
        if iframe:
            src = iframe.get("src", "")
            if not _is_ad_iframe(src):
                result["video_embed"] = src

    # Download links episode
    dl_area = soup.select_one("#download, .download, .gmr-download-list, .soraddlx")
    if dl_area:
        for a in dl_area.select("a[href]"):
            href, text = a.get("href", ""), a.get_text(strip=True)
            if href and "javascript" not in href.lower() and "klik.best" not in href:
                result["download_links"].append({"text": text or "Download", "url": href})

    return result


def extract_iframe_from_page(url: str, browser_path: str = None) -> list[str]:
    """Playwright fallback: Buka halaman dan ambil semua iframe valid (bukan iklan).
    Smart-wait: tunggu .btn-svr muncul untuk DrakorKita, lalu klik dan ambil iframe.
    """
    from playwright.sync_api import sync_playwright

    if not browser_path:
        for candidate in ["/usr/bin/chromium", "/usr/bin/chromium-browser",
                          "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]:
            if os.path.isfile(candidate):
                browser_path = candidate
                break

    iframes = []

    for _retry in range(3):
        try:
            with sync_playwright() as p:
                launch_args = {"headless": True}
                if browser_path:
                    launch_args["executable_path"] = browser_path

                try:
                    browser = p.chromium.launch(**launch_args)
                except Exception:
                    browser = p.chromium.launch(headless=True)

                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768}
                )
                page = ctx.new_page()

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass

                # Smart-wait: tunggu .btn-sv/.btn-svr atau iframe muncul (max 15 detik)
                for _ in range(15):
                    has_btn = page.evaluate("() => document.querySelectorAll('.btn-sv, .btn-svr, .server-btn, .gmr-player-btn').length > 0")
                    has_iframe = page.evaluate("""() => {
                        const iframe = document.querySelector('iframe');
                        return iframe && iframe.src && !iframe.src.startsWith('about:');
                    }""")
                    if has_btn or has_iframe:
                        break
                    page.wait_for_timeout(1000)

                # Klik tombol server untuk memunculkan iframe
                page.evaluate("""() => {
                    let btns = document.querySelectorAll('.btn-sv, .btn-svr, .server-btn, .gmr-player-btn');
                    if (btns.length > 0) btns[0].click();
                }""")

                # Tunggu iframe muncul atau berubah (max 10 detik)
                final_srcs = []
                for _ in range(10):
                    frame_srcs = page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('iframe')).map(f => f.src);
                    }""")
                    clean = [s for s in frame_srcs if s and not _is_ad_iframe(s)]
                    if clean:
                        final_srcs = clean
                        break
                    page.wait_for_timeout(1000)

                if not final_srcs:
                    # Coba sekali lagi setelah total 10 detik
                    frame_srcs = page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('iframe')).map(f => f.src);
                    }""")
                    final_srcs = [s for s in frame_srcs if s and not _is_ad_iframe(s)]

                iframes = final_srcs
                browser.close()
                return iframes

        except Exception as e:
            err_msg = str(e).lower()
            if "execution context" in err_msg or "target closed" in err_msg:
                time.sleep(2)
                continue
            return []

    return []


def _scrape_episodes_with_verification(episodes: list, base_url: str, max_retries: int = 5) -> list:
    """Scrape semua episode secara paralel DENGAN GARANSI verifikasi multi-ronde."""
    log.info(f"     📺 Scraping {len(episodes)} episode secara paralel...")

    browser_path = None
    for candidate in ["/usr/bin/chromium", "/usr/bin/chromium-browser",
                      "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]:
        if os.path.isfile(candidate):
            browser_path = candidate
            break

    # ── RONDE 1: Requests + AJAX (cepat, paralel 8 thread) ──
    results = [None] * len(episodes)

    def _worker(idx, ep):
        ep_data = _scrape_episode_video(ep["url"], base_url)
        return idx, ep_data

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_worker, i, ep): i for i, ep in enumerate(episodes)}
        for f in as_completed(futures):
            try:
                idx, data = f.result()
                results[idx] = {
                    "label": episodes[idx]["label"],
                    "url": episodes[idx]["url"],
                    "video_embed": data["video_embed"],
                    "video_servers": data["video_servers"],
                    "download_links": data["download_links"],
                }
            except Exception as e:
                log.debug(f"     Worker error: {e}")

    # Pastikan semua slot terisi (walau kosong)
    for i in range(len(results)):
        if results[i] is None:
            results[i] = {
                "label": episodes[i]["label"],
                "url": episodes[i]["url"],
                "video_embed": "",
                "video_servers": [],
                "download_links": [],
            }

    filled_r1 = sum(1 for r in results if r.get("video_embed"))
    log.info(f"     → Ronde 1 (AJAX): {filled_r1}/{len(episodes)} episode mendapat video")

    # ── RONDE 2: Re-try AJAX untuk yang gagal (kadang server lambat) ──
    missing = [(i, results[i]) for i in range(len(results)) if not results[i].get("video_embed")]
    if missing:
        log.info(f"     ⚠ {len(missing)} episode belum dapat. Re-try AJAX...")
        time.sleep(1)
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_worker, i, ep): (i, ep) for i, ep in missing}
            for f in as_completed(futures):
                try:
                    idx, data = f.result()
                    if data["video_embed"]:
                        results[idx]["video_embed"] = data["video_embed"]
                        results[idx]["video_servers"] = data["video_servers"]
                        results[idx]["download_links"] = data.get("download_links", [])
                except Exception:
                    pass

        filled_r2 = sum(1 for r in results if r.get("video_embed"))
        log.info(f"     → Ronde 2 (AJAX retry): {filled_r2}/{len(episodes)} episode")

    # ── RONDE 3-5: Playwright fallback untuk yang masih gagal ──
    missing = [(i, results[i]) for i in range(len(results)) if not results[i].get("video_embed")]

    if missing:
        log.info(f"     🔧 {len(missing)} episode masih kosong. Melancarkan Playwright...")
        for retry_round in range(1, max_retries + 1):
            still_missing = []
            for i, ep in missing:
                try:
                    iframes = extract_iframe_from_page(ep["url"], browser_path)
                    if iframes:
                        results[i]["video_embed"] = iframes[0]
                        results[i]["video_servers"] = [{"server": f"pw_{j}", "url": u} for j, u in enumerate(iframes)]
                    else:
                        still_missing.append((i, ep))
                except Exception:
                    still_missing.append((i, ep))

            if not still_missing:
                log.info(f"     ✓ Semua episode 100% terverifikasi! (Playwright ronde {retry_round})")
                break
            missing = still_missing
            if retry_round < max_retries:
                log.info(f"     ⚠ Masih {len(missing)} episode kosong, retry Playwright ronde {retry_round+1}...")
                time.sleep(2)  # Jeda sebelum retry
            else:
                log.info(f"     ⚠ {len(missing)} episode tetap kosong setelah {max_retries} ronde Playwright")

    # ── LAPORAN FINAL ──
    filled = sum(1 for r in results if r.get("video_embed"))
    total = len(episodes)
    pct = round(filled / total * 100, 1) if total else 0
    log.info(f"     ✓ Episode selesai: {filled}/{total} ({pct}%) berhasil diambil videonya")
    if filled < total:
        log.info(f"     ℹ {total - filled} episode mungkin memang belum punya video di server sumber")

    return results


def _scrape_drakorkita_episodes(detail_url: str, total_eps: int) -> list[dict]:
    """Scrape episode video dari site DrakorKita-style menggunakan Playwright.

    Struktur DrakorKita:
    - Series: .btn-svr (id=svr-1,svr-2...) = tombol episode, .btn-sv = tombol server/quality
    - Movie:  tidak ada .btn-svr, hanya .btn-sv, iframe otomatis dimuat

    Untuk series: klik setiap .btn-svr lalu ambil iframe
    Untuk movie: langsung ambil iframe yang sudah dimuat
    """
    from playwright.sync_api import sync_playwright

    browser_path = None
    for candidate in ["/usr/bin/chromium", "/usr/bin/chromium-browser",
                      "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]:
        if os.path.isfile(candidate):
            browser_path = candidate
            break

    ad_domains = AD_DOMAINS

    def _is_ad(url):
        return any(ad in url.lower() for ad in ad_domains) if url else False

    episodes_data = []

    for _pw_attempt in range(3):
        try:
            with sync_playwright() as p:
                launch_args = {"headless": True}
                if browser_path:
                    launch_args["executable_path"] = browser_path

                try:
                    browser = p.chromium.launch(**launch_args)
                except Exception:
                    browser = p.chromium.launch(headless=True)

                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768}
                )
                page = ctx.new_page()

                try:
                    page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass

                # Tunggu halaman render (btn-svr atau btn-sv atau iframe)
                for _ in range(25):
                    ready = page.evaluate("""() => {
                        const hasBtnSvr = document.querySelectorAll('.btn-svr').length > 0;
                        const hasBtnSv = document.querySelectorAll('.btn-sv').length > 0;
                        const hasIframe = !!document.querySelector('iframe[src]:not([src^="about:"])');
                        return hasBtnSvr || hasBtnSv || hasIframe;
                    }""")
                    if ready:
                        break
                    page.wait_for_timeout(1000)

                page.wait_for_timeout(2000)

                def _get_iframe_src():
                    return page.evaluate("""() => {
                        const iframes = document.querySelectorAll('iframe');
                        for (const iframe of iframes) {
                            const src = iframe.src || '';
                            if (src && !src.startsWith('about:')) return src;
                        }
                        return '';
                    }""")

                def _wait_for_new_iframe(prev_src, max_wait=8):
                    for _ in range(max_wait):
                        src = _get_iframe_src()
                        if src and not _is_ad(src) and src != prev_src:
                            return src
                        page.wait_for_timeout(1000)
                    src = _get_iframe_src()
                    return src if src and not _is_ad(src) else ""

                # ── STEP 1: Cek apakah ada tombol episode (.btn-svr) ──
                ep_buttons = page.evaluate("""() => {
                    const btns = document.querySelectorAll('.btn-svr');
                    return Array.from(btns).map((b, i) => ({
                        index: i,
                        text: b.textContent.trim(),
                        id: b.id || ''
                    }));
                }""")

                if ep_buttons and len(ep_buttons) > 0:
                    # ═══════════════════════════════════════════════
                    # MODE SERIES: klik setiap episode .btn-svr
                    # ═══════════════════════════════════════════════
                    log.info(f"     → {len(ep_buttons)} episode buttons ditemukan (.btn-svr)")

                    episodes_data = []
                    seen_srcs = set()

                    for ep in ep_buttons:
                        idx = ep["index"]
                        ep_text = ep.get("text", str(idx + 1))
                        try:
                            ep_label = f"Episode {int(ep_text)}"
                        except ValueError:
                            ep_label = f"Episode {idx + 1}"

                        video_src = ""

                        for _ep_retry in range(3):
                            try:
                                before_src = _get_iframe_src()

                                # Klik tombol episode via ID atau index
                                ep_id = ep.get("id", "")
                                if ep_id:
                                    page.evaluate(f"() => document.getElementById('{ep_id}')?.click()")
                                else:
                                    page.evaluate(f"""() => {{
                                        const btns = document.querySelectorAll('.btn-svr');
                                        if (btns[{idx}]) btns[{idx}].click();
                                    }}""")

                                if idx == 0:
                                    page.wait_for_timeout(2000)
                                    src = _get_iframe_src()
                                    if src and not _is_ad(src):
                                        video_src = src
                                        break
                                    src = _wait_for_new_iframe("", max_wait=8)
                                    if src:
                                        video_src = src
                                        break
                                else:
                                    page.wait_for_timeout(1000)
                                    src = _wait_for_new_iframe(before_src, max_wait=8)
                                    if src and src != before_src:
                                        video_src = src
                                        break
                                    elif src and src not in seen_srcs:
                                        video_src = src
                                        break

                                if _ep_retry < 2:
                                    page.wait_for_timeout(1500)
                            except Exception:
                                if _ep_retry < 2:
                                    page.wait_for_timeout(1000)

                        if video_src:
                            seen_srcs.add(video_src)

                        episodes_data.append({
                            "label": ep_label,
                            "url": detail_url,
                            "video_embed": video_src,
                            "video_servers": [{"server": "main", "url": video_src}] if video_src else [],
                            "download_links": [],
                        })

                else:
                    # ═══════════════════════════════════════════════
                    # MODE MOVIE: tidak ada .btn-svr, langsung ambil iframe
                    # ═══════════════════════════════════════════════
                    log.info(f"     → Movie mode: mengambil iframe langsung")

                    # Klik .btn-sv pertama (jika ada) untuk memastikan player aktif
                    page.evaluate("""() => {
                        const btns = document.querySelectorAll('.btn-sv');
                        if (btns.length > 0) btns[0].click();
                    }""")
                    page.wait_for_timeout(2000)

                    # Ambil iframe
                    src = ""
                    for _ in range(10):
                        src = _get_iframe_src()
                        if src and not _is_ad(src):
                            break
                        page.wait_for_timeout(1000)

                    if src and not _is_ad(src):
                        episodes_data = [{
                            "label": "Movie",
                            "url": detail_url,
                            "video_embed": src,
                            "video_servers": [{"server": "main", "url": src}],
                            "download_links": [],
                        }]
                    else:
                        episodes_data = [{
                            "label": "Movie",
                            "url": detail_url,
                            "video_embed": "",
                            "video_servers": [],
                            "download_links": [],
                        }]

                browser.close()

            # === Ronde 2: Retry episode yang masih kosong (hanya untuk series) ===
            missing_indices = [i for i, e in enumerate(episodes_data) if not e.get("video_embed")]
            if missing_indices and len(ep_buttons) > 0:
                log.info(f"     ⚠ {len(missing_indices)} episode belum dapat. Retry ronde 2...")
                try:
                    with sync_playwright() as p:
                        launch_args = {"headless": True}
                        if browser_path:
                            launch_args["executable_path"] = browser_path
                        try:
                            browser = p.chromium.launch(**launch_args)
                        except Exception:
                            browser = p.chromium.launch(headless=True)

                        ctx = browser.new_context(
                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
                            viewport={"width": 1366, "height": 768}
                        )
                        page = ctx.new_page()
                        try:
                            page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                        except Exception:
                            pass

                        for _ in range(25):
                            btn_count = page.evaluate("() => document.querySelectorAll('.btn-svr').length")
                            if btn_count > 0:
                                break
                            page.wait_for_timeout(1000)
                        page.wait_for_timeout(3000)

                        for miss_idx in missing_indices:
                            try:
                                before_src = page.evaluate("""() => {
                                    const iframe = document.querySelector('iframe');
                                    return (iframe && iframe.src) ? iframe.src : '';
                                }""")

                                # Klik episode via ID
                                ep_id = f"svr-{miss_idx + 1}"
                                page.evaluate(f"""() => {{
                                    let el = document.getElementById('{ep_id}');
                                    if (!el) {{
                                        const btns = document.querySelectorAll('.btn-svr');
                                        el = btns[{miss_idx}];
                                    }}
                                    if (el) el.click();
                                }}""")
                                page.wait_for_timeout(2000)

                                for _ in range(10):
                                    src = page.evaluate("""() => {
                                        const iframes = document.querySelectorAll('iframe');
                                        for (const iframe of iframes) {
                                            const s = iframe.src || '';
                                            if (s && !s.startsWith('about:')) return s;
                                        }
                                        return '';
                                    }""")
                                    if src and not _is_ad(src) and src != before_src:
                                        episodes_data[miss_idx]["video_embed"] = src
                                        episodes_data[miss_idx]["video_servers"] = [{"server": "main", "url": src}]
                                        break
                                    page.wait_for_timeout(1000)

                            except Exception:
                                pass

                        browser.close()
                except Exception:
                    pass

            filled = sum(1 for e in episodes_data if e.get("video_embed"))
            pct = round(filled / len(episodes_data) * 100, 1) if episodes_data else 0
            log.info(f"     ✓ DrakorKita: {filled}/{len(episodes_data)} ({pct}%) berhasil")
            break

        except Exception as e:
            err_msg = str(e).lower()
            if "execution context" in err_msg or "target closed" in err_msg:
                log.debug(f"     ⚠ Browser crash ronde {_pw_attempt+1}/3, restart...")
                time.sleep(2)
                continue
            log.debug(f"     ✗ Playwright error: {e}")
            break

    return episodes_data


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE UTAMA
# ═══════════════════════════════════════════════════════════════════════════════

def run_custom_scrape(url: str, output_name: str = "custom_film"):
    """Pipeline scraper universal: Listing → Detail → Episode → Verifikasi → JSON."""
    log.info(f"🚀 Memindai Struktur URL: {url}")
    timestamp = int(time.time())
    base_url = _get_base_url(url)

    # ── FASE 1: Deteksi — Apakah ini Katalog atau Detail Film? ──
    html = _get_html(url)
    if not html:
        log.error("✗ Gagal memuat URL.")
        return

    soup = BeautifulSoup(html, "html.parser")

    # Cek apakah ini halaman DETAIL (sudah ada episode list atau iframe video)
    ep_check = soup.select(".gmr-listseries a, .episodelist a, ul.lstep li a")
    iframe_check = soup.select_one("iframe[src]")
    has_iframe = iframe_check and not _is_ad_iframe(iframe_check.get("src", ""))

    found_films = []

    if ep_check or has_iframe:
        # Halaman DETAIL tunggal
        title = ""
        h1 = soup.select_one("h1.entry-title, h1")
        if h1:
            title = _title_clean(h1.get_text(strip=True))
        found_films = [{"title": title or "Unknown", "detail_url": url}]
        log.info(f"✓ URL terdeteksi sebagai Halaman Detail Film (Episode: {len(ep_check)}, Iframe: {'✓' if has_iframe else '✗'})")
    else:
        # Halaman KATALOG — crawl listing
        log.info("✓ URL terdeteksi sebagai Halaman Katalog. Memulai crawling...")
        found_films = crawl_film_listings(url)  # Tanpa batas — crawl sampai habis

        if not found_films:
            log.error("✗ Tidak ada film ditemukan di halaman ini.")
            return

    log.info(f"✓ Total {len(found_films)} judul ditemukan.\n")

    # ── FASE 2: Tanya User ──
    def _ask(prompt, default=""):
        suffix = f" [{default}]" if default else ""
        try:
            val = input(f"  ?  {prompt}{suffix}: ").strip()
            return val if val else default
        except (KeyboardInterrupt, EOFError):
            return default

    if len(found_films) > 1:
        # Tampilkan preview
        for i, f in enumerate(found_films[:8]):
            title_short = f['title'][:60] if f.get('title') else 'Unknown'
            print(f"  {i+1}. {title_short}")
        if len(found_films) > 8:
            print(f"  ... dan {len(found_films) - 8} lainnya.")
        print()

        limit_str = _ask(f"Berapa film yang ingin di-scrape? (1-{len(found_films)}, 'all' untuk semua)", "all")
        if limit_str.lower() != 'all':
            try:
                limit = int(limit_str)
                found_films = found_films[:limit]
            except ValueError:
                log.error("Input tidak valid, membatalkan.")
                return

    # ── FASE 3: Scrape Detail + Episode secara Paralel ──
    log.info(f"\n🚀 Memulai Deep-Scrape {len(found_films)} Film/Drama...\n")

    all_results = []
    lock = threading.Lock()
    completed = [0]

    def _process_film(film_info):
        f_url = film_info.get("detail_url", film_info.get("url", ""))
        f_title = film_info.get("title", "Unknown")

        try:
            # Scrape detail halaman film
            detail = scrape_detail(f_url, base_url)

            # Gunakan judul dari listing jika detail gagal mendapat judul
            if not detail["title"]:
                detail["title"] = f_title

            # Jika ada episode, scrape semua episode dengan verifikasi
            if detail["episodes"] and not detail.get("_needs_playwright_episodes"):
                # Episode punya URL (GMR/WP theme) → scrape per episode
                ep_results = _scrape_episodes_with_verification(
                    detail["episodes"], base_url, max_retries=2
                )
                detail["episode_embeds"] = ep_results
                detail["total_episodes"] = len(ep_results)

            elif detail.get("_needs_playwright_episodes"):
                # DrakorKita: Episode JS-rendered via .btn-svr buttons
                # Gunakan Playwright untuk klik setiap episode dan ambil iframe
                ep_count = detail["total_episodes"]
                log.info(f"     📺 DrakorKita mode: {ep_count} episode via Playwright...")
                ep_results = _scrape_drakorkita_episodes(f_url, ep_count)
                detail["episode_embeds"] = ep_results
                detail["total_episodes"] = len(ep_results)
                del detail["_needs_playwright_episodes"]

            else:
                # Film biasa tanpa episode — video embed sudah diambil di scrape_detail
                detail["episode_embeds"] = []

                # Jika AJAX + static iframe gagal, coba Playwright sebagai backup
                if not detail["video_embed"]:
                    # DrakorKita movie: gunakan handler khusus yang lebih robust
                    if "/detail/" in f_url:
                        log.info(f"     🎬 DrakorKita movie mode: mengambil video via Playwright...")
                        try:
                            ep_results = _scrape_drakorkita_episodes(f_url, 1)
                            if ep_results and ep_results[0].get("video_embed"):
                                detail["video_embed"] = ep_results[0]["video_embed"]
                                detail["video_servers"] = ep_results[0].get("video_servers", [])
                        except Exception:
                            pass

                    # Fallback umum untuk non-DrakorKita
                    if not detail["video_embed"]:
                        try:
                            iframes = extract_iframe_from_page(f_url)
                            if iframes:
                                detail["video_embed"] = iframes[0]
                                detail["video_servers"] = [{"server": f"pw_{j}", "url": u} for j, u in enumerate(iframes)]
                        except Exception:
                            pass

            with lock:
                all_results.append(detail)
                completed[0] += 1

                # Log progress
                v = 1 if detail.get("video_embed") else 0
                e = detail.get("total_episodes", 0)
                ep_filled = sum(1 for ep in detail.get("episode_embeds", []) if ep and ep.get("video_embed"))
                dl = len(detail.get("download_links", []))
                g = len(detail.get("genres", []))

                parts = []
                if e:
                    parts.append(f"📺 {ep_filled}/{e} Eps")
                if v:
                    parts.append(f"🎬 Video ✓")
                if dl:
                    parts.append(f"📥 {dl} DL")
                if g:
                    parts.append(f"🏷️ {g} Genre")

                summary = " | ".join(parts) if parts else "⚠ Minimal data"
                title_short = detail['title'][:45] + "..." if len(detail['title']) > 45 else detail['title']
                log.info(f"  ✓ [{completed[0]}/{len(found_films)}] {title_short} — {summary}")

        except Exception as exc:
            with lock:
                completed[0] += 1
                log.error(f"  ✗ [{completed[0]}/{len(found_films)}] {f_title}: {exc}")

    # DrakorKita sites perlukan Playwright — batasi ke 2 worker agar browser tidak crash
    # Non-DrakorKita bisa 5 worker (HTTP only, ringan)
    is_drakorkita = any("/detail/" in f.get("detail_url", "") for f in found_films[:3])
    workers = 2 if is_drakorkita else min(len(found_films), 5)
    log.info(f"  ℹ Paralel: {workers} worker {'(DrakorKita — Playwright mode)' if is_drakorkita else ''}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        tasks = {executor.submit(_process_film, f): f for f in found_films}
        try:
            for future in as_completed(tasks):
                future.result()
        except KeyboardInterrupt:
            log.warning("⚠ Dibatalkan user.")
            executor.shutdown(wait=False, cancel_futures=True)

    # ── SIMPAN HASIL ──
    safe_name = re.sub(r'[^A-Za-z0-9_]+', '_', output_name).strip('_').lower()
    full_path = os.path.join(OUTPUT_DIR, f"{safe_name}_{timestamp}.json")

    output_data = {
        "metadata": {
            "scraper_name": "Universal Film Scraper v3.0",
            "source_url": url,
            "scrape_date": datetime.now().isoformat(),
            "total_films": len(all_results),
            "execution_time_sec": round(time.time() - timestamp, 1),
        },
        "data": all_results
    }

    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    size_kb = round(os.path.getsize(full_path) / 1024, 1)

    log.info(f"\n{'═'*60}")
    log.info(f"✓ SELESAI UNIVERSAL SCRAPE!")
    log.info(f"  Berhasil: {len(all_results)} Judul")
    log.info(f"  File: {full_path} ({size_kb} KB)")
    log.info(f"{'═'*60}")
