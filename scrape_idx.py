import os
import sys
import json
import time
import logging
from playwright.sync_api import sync_playwright

# Inisialisasi Project (Path system)
sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.append(sys_path)

from config import settings

logger = logging.getLogger(__name__)

# Constants
IDX_BASE_URL = "https://www.idx.co.id/id/"
API_STOCKS_URL = "https://www.idx.co.id/primary/StockData/GetSecuritiesStock?start=0&length=9999&language=id-id"
API_SUMMARY_URL = "https://www.idx.co.id/primary/TradingSummary/GetStockSummary?start=0&length=9999"
API_BROKER_URL = "https://www.idx.co.id/primary/TradingSummary/GetBrokerSummary?start=0&length=9999"

def _get_browser_path():
    """Cari lokasi browser chromium di sistem sebagai fallback."""
    for p in ["/usr/bin/chromium", "/usr/bin/chromium-browser",
              "/usr/bin/google-chrome",
              "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
              "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"]:
        if os.path.exists(p):
            return p
    return None

def fetch_idx_data_via_browser() -> dict:
    """Menggunakan browser secara native untuk fetch API tanpa repot curi token."""
    logger.info("Membuka sesi Browser (Playwright) untuk scrape data IDX...")
    
    stocks_meta_data = []
    stocks_summary_data = []
    broker_summary_data = []

    with sync_playwright() as p:
        launch_kwargs = {
            "headless": settings.HEADLESS,
            "args": ["--disable-blink-features=AutomationControlled"]
        }
        
        sys_browser = _get_browser_path()
        if sys_browser:
            launch_kwargs["executable_path"] = sys_browser

        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception:
            launch_kwargs.pop("executable_path", None)
            browser = p.chromium.launch(**launch_kwargs)
            
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        try:
            logger.info("Mengakses homepage IDX untuk mendapatkan otentikasi...")
            page.goto(IDX_BASE_URL, timeout=40000, wait_until="networkidle")
            page.wait_for_timeout(3000)

            # --- FETCH 1: DAFTAR SAHAM ---
            logger.info("Mengeksekusi native fetch ke API Daftar Saham...")
            stocks_meta_res = page.evaluate(f'''async () => {{
                try {{
                    const res = await fetch("{API_STOCKS_URL}");
                    return await res.json();
                }} catch (e) {{ return {{error: e.toString()}}; }}
            }}''')
            if not stocks_meta_res.get("error"):
                stocks_meta_data = stocks_meta_res.get("data", [])
                logger.info(f" ✓ {len(stocks_meta_data)} emiten saham berhasil diunduh.")

            # --- FETCH 2: RINGKASAN PERDAGANGAN ---
            logger.info("Mengeksekusi native fetch ke API Ringkasan Perdagangan...")
            stocks_summary_res = page.evaluate(f'''async () => {{
                try {{
                    const res = await fetch("{API_SUMMARY_URL}");
                    return await res.json();
                }} catch (e) {{ return {{error: e.toString()}}; }}
            }}''')
            if not stocks_summary_res.get("error"):
                stocks_summary_data = stocks_summary_res.get("data", [])
                logger.info(f" ✓ {len(stocks_summary_data)} ringkasan saham berhasil diunduh.")

            # --- FETCH 3: RINGKASAN BROKER ---
            logger.info("Mengeksekusi native fetch ke API Ringkasan Broker...")
            broker_summary_res = page.evaluate(f'''async () => {{
                try {{
                    const res = await fetch("{API_BROKER_URL}");
                    return await res.json();
                }} catch (e) {{ return {{error: e.toString()}}; }}
            }}''')
            if not broker_summary_res.get("error"):
                broker_summary_data = broker_summary_res.get("data", [])
                logger.info(f" ✓ {len(broker_summary_data)} ringkasan broker berhasil diunduh.")

        except Exception as e:
            logger.error(f"Terjadi kesalahan saat mengeksekusi fetch dari dalam browser: {e}")
        finally:
            browser.close()

    return {
        "metadata": stocks_meta_data,
        "summary": stocks_summary_data,
        "brokers": broker_summary_data
    }

def scrape_idx_all() -> dict:
    """Fungsi pembungkus untuk memproses data mentah dari native fetch."""
    
    raw = fetch_idx_data_via_browser()
    if not raw["metadata"] and not raw["summary"]:
        logger.error("Scraping IDX gagal sepenuhnya (0 data).")
        return None

    logger.info("Memproses dan menggabungkan data...")
    combined_stocks = {}

    # Masukkan metadata dulu (Kode, Nama, Papan, Saham_Beredar)
    for s in raw["metadata"]:
        code = s.get("Code")
        if code:
            combined_stocks[code] = {
                "Kode": code,
                "Nama_Perusahaan": s.get("Name", ""),
                "Sektor": s.get("Sector", ""),
                "Papan_Pencatatan": s.get("Board", ""),
                "Saham_Beredar": s.get("Shares", 0),
                "Tanggal_Pencatatan": s.get("ListingDate", "")
            }

    # Merge dengan data trading (Harga, Volume, Nilai)
    for s in raw["summary"]:
        code = s.get("StockCode")
        if code:
            if code not in combined_stocks:
                combined_stocks[code] = {"Kode": code}
                
            combined_stocks[code].update({
                "Harga_Tinggi": s.get("High", 0),
                "Harga_Rendah": s.get("Low", 0),
                "Harga_Tutup": s.get("Close", 0),
                "Selisih": s.get("Change", 0),
                "Persentase_Selisih": s.get("Percentage", 0),
                "Volume": s.get("Volume", 0),
                "Nilai": s.get("Value", 0),
                "Frekuensi": s.get("Frequency", 0)
            })

    output_data = {
        "metadata": {
            "scraper_name": "IDX Native Browser API Scraper",
            "timestamp": int(time.time()),
            "total_stocks": len(combined_stocks),
            "total_brokers": len(raw["brokers"])
        },
        "stocks": combined_stocks,
        "brokers": raw["brokers"]
    }

    return output_data

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print("\n[IDX SCRAPER TEST]")
    res = scrape_idx_all()
    if res:
        out_dir = os.path.join(sys_path, "hasil_scrape")
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"idx_combined_{int(time.time())}.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"\n✅ Berhasil! Data tersimpan di: {out_file}")
        print(f"   Total Saham: {res['metadata']['total_stocks']}")
        print(f"   Total Broker: {res['metadata']['total_brokers']}")
