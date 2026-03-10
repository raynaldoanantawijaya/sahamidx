import logging
import time
import random
import os
from playwright.sync_api import sync_playwright

# Adjust import to relative or absolute. We will use absolute from config.
import sys
sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path not in sys.path:
    sys.path.append(sys_path)
    
from config import settings
from modules.network_capture import CaptureResult  # Optional type hint

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

def simulate_and_capture(url, stealth_config=None, proxies=None):
    """
    Membuka halaman dan mensimulasikan interaksi cerdas manusia seperti klik 'Load More',
    scrolling acak, dan pergerakan mouse untuk memicu endpoint tersembunyi.
    Merekam traffic seperti di `network_capture.py`.
    """
    logger.info(f"Memulai Simulasi Interaksi untuk memancing data pada: {url}")
    result = CaptureResult()

    with sync_playwright() as p:
        browser_type = p.chromium
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
        }
        
        session_file = None
        if settings.SAVE_SESSION:
            safe_domain = url.split("//")[-1].split("/")[0]
            session_file = os.path.join(settings.SESSION_DIR, f"{safe_domain}_state.json")
            if os.path.exists(session_file):
                context_options["storage_state"] = session_file
                
        context = browser.new_context(**context_options)
        
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        if stealth_config and callable(stealth_config):
            stealth_config(context)

        page = context.new_page()

        # Listeners untuk capture
        def handle_response(response):
            if response.request.resource_type in ["fetch", "xhr", "document"]:
                try:
                    content_type = response.headers.get("content-type", "").lower()
                    if "json" in content_type or "text" in content_type:
                        body_decoded = response.body().decode('utf-8', errors='ignore')
                        result.responses.append({
                            "url": response.url,
                            "status": response.status,
                            "content_type": content_type,
                            "body": body_decoded,
                            "type": response.request.resource_type
                        })
                except Exception:
                    pass

        def handle_websocket(ws):
            def frame_received(frame):
                result.websocket_messages.append({"url": ws.url, "direction": "received", "data": frame})
            def frame_sent(frame):
                result.websocket_messages.append({"url": ws.url, "direction": "sent", "data": frame})
            ws.on("framereceived", frame_received)
            ws.on("framesent", frame_sent)

        page.on("response", handle_response)
        page.on("websocket", handle_websocket)

        try:
            page.goto(url, timeout=settings.TIMEOUT, wait_until="networkidle")
            
            # --- MULAI SIMULASI INTERAKSI HUMAN-LIKE ---
            
            # 1. Random Mouse Move (Simulasi awal)
            page.mouse.move(random.randint(100, 800), random.randint(100, 800))
            page.wait_for_timeout(random.randint(1000, 2000))
            
            # 2. Scrolling Bertahap Kebawah
            logger.info("Melakukan scrolling bertahap...")
            for i in range(3):
                page.mouse.wheel(0, random.randint(300, 700))
                page.wait_for_timeout(random.randint(1500, 3000))
                
            # 3. Klik Element "Load More", "Muat", dll.
            logger.info("Mencari tombol interaksi (Load More/Selengkapnya)...")
            button_texts = ['Muat', 'Load', 'Lainnya', 'Selengkapnya', 'Lebih', 'Show', 'Next', 'Selanjutnya']
            
            for text in button_texts:
                try:
                    # Cari elemen yang mengandung teks tersebut (case-insensitive di handle oleh css xpath text matching basic Playwright)
                    elements = page.locator(f"text=/(?i){text}/").all()
                    for el in elements:
                        if el.is_visible():
                            logger.debug(f"Mengklik elemen dengan teks '{text}'")
                            # hover dulu
                            el.hover()
                            page.wait_for_timeout(random.randint(500, 1500))
                            el.click()
                            page.wait_for_timeout(random.randint(2000, 4000)) # Tunggu loading
                except Exception:
                    pass
            
            # 4. Save state
            result.html_content = page.content()
            if session_file:
                context.storage_state(path=session_file)
                
        except Exception as e:
            logger.error(f"Gagal saat interaksi di {url}: {e}")
        finally:
            browser.close()
            
    return result
