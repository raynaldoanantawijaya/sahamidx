import os
import sys
import json
import time
import requests
from bs4 import BeautifulSoup
from colorama import init, Fore, Style
import logging

# Inisialisasi Project (Path system)
sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.append(sys_path)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hasil_scrape")
os.makedirs(OUTPUT_DIR, exist_ok=True)
from scrape_custom_film import extract_iframe_from_page

init(autoreset=True)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

def ok(msg): print(f"{Fore.GREEN}✓ {msg}{Style.RESET_ALL}")
def info(msg): print(f"{Fore.CYAN}ℹ {msg}{Style.RESET_ALL}")
def err(msg): print(f"{Fore.RED}✗ {msg}{Style.RESET_ALL}")

def get_html(url: str):
    """Fungsi helper untuk request HTML dengan error handling standar."""
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.raise_for_status()
        return res.text
    except Exception as e:
        logger.debug(f"Error request '{url}': {e}")
        return None

def extract_movie_list(url: str, max_pages: int = 1) -> list:
    """Mengambil daftar film (judul, link detail, poster, rating) dari halaman kategori/home."""
    movies = []
    base_url = url.rstrip('/')

    for page in range(1, max_pages + 1):
        target_url = f"{base_url}/page/{page}/" if page > 1 else base_url
        info(f"Membuka halaman {page}: {target_url}")
        
        html = get_html(target_url)
        if not html:
            err(f"Gagal memuat halaman {page}.")
            break

        soup = BeautifulSoup(html, "html.parser")
        
        # Tema WordPress rebahin/lk21 biasanya menggunakan <article class="item">
        items = soup.find_all("article", class_="item")
        if not items:
            items = soup.select(".gmr-box-content") # Alternatif selektor jika menggunakan theme GMR

        if not items:
            err(f"Tidak ada film ditemukan di halaman {page}. Parsing selesai.")
            break

        for item in items:
            # Cari elemen A (link) utama
            a_tag = item.find("a", href=True)
            if not a_tag:
                continue
            
            detail_url = a_tag["href"]
            
            # Cari Judul
            title = ""
            title_node = item.find(["h2", "h3", "h4"], class_="entry-title")
            if title_node:
                title = title_node.text.strip()
            # Fallback ke atribut title dari A tag atau img alt
            if not title:
                title = a_tag.get("title", "")
                
            # Cari Rating (jika ada)
            rating = ""
            rating_node = item.find("div", class_="gmr-rating-item") or item.select_one(".rating")
            if rating_node:
                rating = rating_node.text.strip()
                
            # Kualitas (HD, Bluray, CAM, dll)
            quality = ""
            quality_node = item.find("div", class_="gmr-quality-item") or item.select_one(".quality")
            if quality_node:
                quality = quality_node.text.strip()

            video_data = {
                "title": title or "Unknown Title",
                "detail_url": detail_url,
                "rating": rating,
                "quality": quality,
                "source": "Azarug"
            }
            movies.append(video_data)
            
    return movies

def extract_movie_details(movie: dict) -> dict:
    """Masuk ke halaman detail film dan mengekstrak sinopsis, info pemeran, iframe video, dan link download."""
    url = movie["detail_url"]
    html = get_html(url)
    if not html:
        return movie
        
    soup = BeautifulSoup(html, "html.parser")
    
    # Memperbarui judul dari H1 utama jika belum akurat
    h1 = soup.find("h1", class_="entry-title")
    if h1:
        movie["title"] = h1.text.strip()

    # 1. Ekstrak Deskripsi/Sinopsis
    sinopsis_node = soup.find("div", class_="entry-content")
    if sinopsis_node:
        paragraphs = sinopsis_node.find_all("p")
        # Hindari paragraf pendek yang biasanya cuma link tag/iklan
        valid_p = [p.text.strip() for p in paragraphs if len(p.text.strip()) > 30]
        if valid_p:
            movie["sinopsis"] = "\\n".join(valid_p)

    # 2. Ekstrak Metadata Film (Sutradara, Genre, Aktor)
    # Biasanya ada dalam daftar list item
    meta_info = soup.select(".gmr-movie-meta-list:not(.gmr-movie-meta-list-bottom) .gmr-movie-meta")
    for meta in meta_info:
        label_node = meta.find("strong")
        if not label_node:
            continue
        label = label_node.text.replace(":", "").strip().lower()
        val = meta.text.replace(label_node.text, "").strip()
        
        if "genre" in label:
            movie["genres"] = [g.strip() for g in val.split(",")]
        elif "aktor" in label or "cast" in label:
            movie["cast"] = [c.strip() for c in val.split(",")]
        elif "sutradara" in label or "director" in label:
            movie["directors"] = [d.strip() for d in val.split(",")]
        elif "rilis" in label or "release" in label:
            movie["release_date"] = val

    # 3. Ekstrak Video Player Embeds (Iframes) dengan Headless Playwright
    player_links = []
    
    # Deteksi Episode List (jika ini halaman series)
    episodes = []
    ep_list = soup.select(".gmr-listseries a")
    if ep_list:
        for a in ep_list:
            ep_url = a.get("href")
            ep_title = a.text.strip()
            if ep_url and ep_title:
                episodes.append({"label": ep_title, "url": ep_url, "video_embeds": []})

    if episodes:
        # Jika ada episode, kita biarkan logic scraping paralel diurus oleh caller
        # Tapi untuk simpelnya, kita cukup list episodenya di sini.
        movie["episodes"] = episodes
    else:
        # Panggil fungsi Playwright tangguh kita untuk mengekstrak iframe dari halaman ini
        try:
            player_links = extract_iframe_from_page(url)
        except Exception as e:
            logger.error(f"Error playwright di {url}: {e}")
            
    movie["video_players"] = player_links

    # 4. Ekstrak Download Links
    # Struktur WordPress streaming biasa menggunakan div '#download' atau '.download' atau '.gmr-download-list'
    downloads = []
    download_area = soup.select_one("#download, .download, .gmr-download-list, .soraddlx")
    
    if download_area:
        download_links = download_area.find_all("a", href=True)
        for d in download_links:
            text = d.text.strip()
            href = d["href"]
            if text and href and "javascript" not in href.lower():
                downloads.append({
                    "description": text,
                    "url": href
                })
    else:
        # Fallback cari tombol yang ada teks 'download'
        for a in soup.find_all("a", href=True):
            if "download" in a.text.lower() or "unduh" in a.text.lower():
                downloads.append({
                    "description": a.text.strip(),
                    "url": a["href"]
                })
                
    movie["download_links"] = downloads

    return movie

def scrape_azarug(target_url: str, limit: int = 10, max_pages: int = 1, show_progress=True) -> dict:
    """Scrape penuh Azarug (Daftar -> Detail -> JSON)."""
    
    start_time = time.time()
    
    # Tahap 1: Ekstraksi Daftar Film
    movies_list = extract_movie_list(target_url, max_pages=max_pages)
    movies_to_process = movies_list[:limit]
    
    if show_progress:
        ok(f"Berhasil mengumpulkan {len(movies_to_process)} link film untuk diproses (dari total {len(movies_list)}).")
    
    # Tahap 2: Ekstraksi Detail tiap Film secara Paralel (Async / ThreadPool)
    results = []
    
    import concurrent.futures
    max_threads = 10 # Maksimal 10 koneksi bersamaan agar tidak terkena Rate Limit terlampau parah
    
    if show_progress:
        info(f"Memulai ekstrak detail secara paralel menggunakan {max_threads} Threads...")

    def fetch_detail(m):
        try:
            return extract_movie_details(m)
        except Exception as e:
            err(f"Gagal ekstrak detail {m['title']}: {e}")
            return m

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        # Menjalankan pemrosesan secara paralel
        future_to_movie = {executor.submit(fetch_detail, m): m for m in movies_to_process}
        
        for future in concurrent.futures.as_completed(future_to_movie):
            detailed_movie = future.result()
            
            # Tambahan: Jika ini adalah Series (punya episodes), kita ekstrak iframenya secara paralel!
            if detailed_movie.get("episodes"):
                ep_list = detailed_movie["episodes"]
                
                # Gunakan 3 thread per film untuk episode agar komputer tidak lag parah
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex_ep:
                    def _ep_worker(ep):
                        ep["video_embeds"] = extract_iframe_from_page(ep["url"])
                        
                    future_eps = [ex_ep.submit(_ep_worker, ep) for ep in ep_list]
                    concurrent.futures.wait(future_eps)
                    
            results.append(detailed_movie)
            
            if show_progress:
                title_short = detailed_movie['title'][:50] + "..." if len(detailed_movie['title']) > 50 else detailed_movie['title']
                
                # Log Summary
                v_count = len(detailed_movie.get("video_players", []))
                e_count = len(detailed_movie.get("episodes", []))
                dl_count = len(detailed_movie.get("download_links", []))
                
                sum_parts = []
                if e_count: sum_parts.append(f"📺 {e_count} Eps")
                if v_count: sum_parts.append(f"🎬 {v_count} Vid")
                if dl_count: sum_parts.append(f"📥 {dl_count} DL")
                
                summary = " | ".join(sum_parts)
                print(f"  {Fore.GREEN}✓{Style.RESET_ALL} [{len(results)}/{len(movies_to_process)}] {title_short} — {summary}")

    elapsed = round(time.time() - start_time, 1)
    if show_progress:
        ok(f"Scrape selesai dalam {elapsed} detik!")

    output_data = {
        "metadata": {
            "scraper_name": "Azarug (WordPress Series) Scraper",
            "timestamp": int(time.time()),
            "source": target_url,
            "total_items": len(results),
            "execution_time_sec": elapsed
        },
        "data": results
    }
    
    return output_data

if __name__ == "__main__":
    import os
    import json
    import time
    from colorama import Fore, Style
    from utils import get_html, ok, info, err, OUTPUT_DIR

    url = "https://azarug.org/"
    res = scrape_azarug(url, limit=2, show_progress=True)
    
    if res and res["data"]:
        safe_name = f"azarug_{int(time.time())}"
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            
        full_path = os.path.join(OUTPUT_DIR, safe_name + ".json")
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2) # Changed output_data to res

        size_kb = round(os.path.getsize(full_path) / 1024, 1)
        
        print(f"\n  {Fore.GREEN}═════════════════════════════════════════════════════════")
        print(f"  ✓  AZARUG SCRAPE BERHASIL")
        print(f"  ✓  Jumlah data: {len(res['data'])} item") # Changed results to res['data']
        print(f"  ✓  File: {full_path}")
        print(f"  ═════════════════════════════════════════════════════════{Style.RESET_ALL}\n")
        
        print(f"\n{Fore.YELLOW}Preview Detail Pertama:{Style.RESET_ALL}")
        print(json.dumps(res["data"][0], indent=2, ensure_ascii=False))

