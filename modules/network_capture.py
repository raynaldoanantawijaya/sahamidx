import logging
import time
import os
import json
from playwright.sync_api import sync_playwright
# Adjust import to relative or absolute. We will use absolute from config.
sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path not in os.sys.path:
    os.sys.path.append(sys_path)
    
from config import settings

logger = logging.getLogger(__name__)


def _get_browser_path():
    """Cari lokasi browser chromium di sistem sebagai fallback."""
    for p in ["/usr/bin/chromium", "/usr/bin/chromium-browser",
              "/usr/bin/google-chrome",
              "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
              "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe"]:
        if os.path.exists(p):
            return p
    return None

class CaptureResult:
    def __init__(self):
        self.responses = []
        self.websocket_messages = []
        self.har_file = None
        self.html_content = ""

def capture(url, stealth_config=None, proxies=None):
    """
    Membuka halaman dengan Playwright, merekam semua response HTTP XHR/Fetch dan WebSocket.
    Menyimpan HAR jika diaktifkan.
    Gunakan stealth_config untuk Anti-Deteksi (dipassing dari anti_detect.py jika ada).
    """
    logger.info(f"Memulai Network Capture untuk: {url}")
    result = CaptureResult()
    
    har_path = None
    if settings.SAVE_HAR:
        timestamp = int(time.time())
        # Ubah nama domain menjadi string aman untuk file
        safe_domain = "".join([c if c.isalnum() else "_" for c in url])[:50]
        har_path = os.path.join(settings.HAR_DIR, f"{safe_domain}_{timestamp}.har")
        result.har_file = har_path

    # Konfigurasi Sesi
    session_file = None
    if settings.SAVE_SESSION:
        safe_domain = url.split("//")[-1].split("/")[0]
        session_file = os.path.join(settings.SESSION_DIR, f"{safe_domain}_state.json")

    with sync_playwright() as p:
        browser_type = p.chromium
        # Custom args untuk bypass ringan
        args = ["--disable-blink-features=AutomationControlled"]
        
        launch_kwargs = {
            "headless": settings.HEADLESS,
            "args": args,
        }
        if proxies:
            launch_kwargs["proxy"] = proxies

        # Fallback ke browser sistem jika Playwright managed browser tidak ada
        sys_browser = _get_browser_path()
        if sys_browser:
            launch_kwargs["executable_path"] = sys_browser
            logger.info(f"Menggunakan browser sistem: {sys_browser}")

        try:
            browser = browser_type.launch(**launch_kwargs)
        except Exception as e:
            logger.warning(f"Gagal launch dengan executable_path: {e}")
            launch_kwargs.pop("executable_path", None)
            browser = browser_type.launch(**launch_kwargs)
        
        context_options = {
            "viewport": {"width": 1920, "height": 1080},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "record_har_path": har_path,
            "bypass_csp": True
        }
        
        # Load sesi jika ada
        if session_file and os.path.exists(session_file):
            logger.info("Memuat state sesi yang tersimpan.")
            context_options["storage_state"] = session_file
            
        context = browser.new_context(**context_options)
        
        # Anti-detect script injection
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        if stealth_config and callable(stealth_config):
            stealth_config(context)

        page = context.new_page()

        # Listeners
        def handle_response(response):
            if response.request.resource_type in ["fetch", "xhr", "document"]:
                try:
                    content_type = response.headers.get("content-type", "").lower()
                    if "json" in content_type or "text" in content_type:
                        body_bytes = response.body()
                        body_decoded = body_bytes.decode('utf-8', errors='ignore')
                        result.responses.append({
                            "url": response.url,
                            "status": response.status,
                            "content_type": content_type,
                            "body": body_decoded,
                            "type": response.request.resource_type
                        })
                        logger.debug(f"Captured response dari: {response.url}")
                except Exception as e:
                    pass # Body mungkin belum siap atau error decode

        def handle_websocket(ws):
            logger.debug(f"Pesan WebSocket ditangkap pada URL: {ws.url}")
            def frame_received(frame):
                result.websocket_messages.append({"url": ws.url, "direction": "received", "data": frame})
            def frame_sent(frame):
                result.websocket_messages.append({"url": ws.url, "direction": "sent", "data": frame})
                
            ws.on("framereceived", frame_received)
            ws.on("framesent", frame_sent)

        page.on("response", handle_response)
        page.on("websocket", handle_websocket)

        try:
            logger.info("Membuka halaman (tunggu hingga network idle)...")
            page.goto(url, timeout=settings.TIMEOUT, wait_until="networkidle")
            
            # Beri jeda tambahan agar script di halaman dieksekusi sempurna
            page.wait_for_timeout(3000)
            
            # Simpan hasil HTML (untuk ekstraksi Inline JSON atau JS)
            result.html_content = page.content()
            
            # Simpan sesi setelah page load
            if session_file:
                context.storage_state(path=session_file)
                logger.info("Menyimpan state sesi.")
                
        except Exception as e:
            logger.error(f"Gagal melakukan capture pada {url}: {e}")
            
        finally:
            browser.close()
            
    if har_path:
        logger.info(f"HAR tersimpan di: {har_path}")
        
    return result

def native_browser_fetch(main_url: str, api_endpoints: list, stealth_config=None, proxies=None) -> dict:
    """Teknik IDX Asli: Eksekusi fetch() langsung secara natif di browser Playwright yang terotentikasi."""
    logger.info(f"Memulai Native Browser Fetch (Layer 3) melalui {main_url}...")
    fetched_data = {}
    
    with sync_playwright() as p:
        browser_type = p.chromium
        args = ["--disable-blink-features=AutomationControlled"]
        
        launch_kwargs = {"headless": settings.HEADLESS, "args": args}
        if proxies: launch_kwargs["proxy"] = proxies
        
        sys_browser = _get_browser_path()
        if sys_browser: launch_kwargs["executable_path"] = sys_browser

        try:
            browser = browser_type.launch(**launch_kwargs)
        except Exception:
            launch_kwargs.pop("executable_path", None)
            browser = browser_type.launch(**launch_kwargs)
            
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        if stealth_config and callable(stealth_config):
            stealth_config(context)
            
        page = context.new_page()

        try:
            logger.info("Mengakses main URL untuk memuat Cookie/Token Vue/React...")
            page.goto(main_url, timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(3000)

            for endpoint in api_endpoints:
                logger.debug(f"Mengeksekusi native fetch ke: {endpoint}")
                fetch_res = page.evaluate(f'''async () => {{
                    try {{
                        const res = await fetch("{endpoint}");
                        return await res.json();
                    }} catch (e) {{ return {{error: e.toString()}}; }}
                }}''')
                
                if isinstance(fetch_res, dict) and not fetch_res.get("error"):
                    fetched_data[endpoint] = fetch_res
                    logger.info(f"Native fetch berhasil mengekstrak data dari: {endpoint}")
                else:
                    logger.warning(f"Native fetch gagal untuk {endpoint}: {fetch_res}")

        except Exception as e:
            logger.error(f"Terjadi kesalahan saat Native Browser Fetch: {e}")
        finally:
            browser.close()

    return fetched_data
