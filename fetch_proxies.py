import os
import sys
import json
import time
import requests
import concurrent.futures
from urllib.parse import urlparse

# Add project root to path
sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

try:
    from colorama import init, Fore, Style
    init(autoreset=True)
except ImportError:
    class FakeColor:
        def __getattr__(self, name): return ""
    Fore = Style = FakeColor()

from config import settings

def ok(msg): print(f"{Fore.GREEN}✓ {msg}{Style.RESET_ALL}")
def err(msg): print(f"{Fore.RED}✗ {msg}{Style.RESET_ALL}")
def info(msg): print(f"{Fore.CYAN}ℹ {msg}{Style.RESET_ALL}")
def warn(msg): print(f"{Fore.YELLOW}⚠ {msg}{Style.RESET_ALL}")

# Parameter Test
TEST_URL = "http://httpbin.org/ip"
TEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json"
}
TIMEOUT = 10
MAX_PROXIES_NEEDED = 10

PROXY_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all"
]

def fetch_raw_proxies():
    """Mengambil daftar mentah proxy dari public feeds."""
    info("Mengunduh daftar proxy publik terbaru...")
    raw_proxies = set()
    
    for url in PROXY_SOURCES:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                lines = r.text.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if line and ':' in line:
                        # Format as http://ip:port
                        if not line.startswith("http"):
                            raw_proxies.add(f"http://{line}")
                        else:
                            raw_proxies.add(line)
        except Exception as e:
            warn(f"Gagal mengambil proxy dari {url}: {e}")
            
    return list(raw_proxies)

def test_proxy(proxy_url):
    """Mengetes 1 proxy untuk mengecek apakah proxy beroperasi (Alive)."""
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    }
    
    try:
        r = requests.get(TEST_URL, headers=TEST_HEADERS, proxies=proxies, timeout=TIMEOUT)
        if r.status_code == 200:
            return proxy_url
    except Exception:
        pass
    return None

def main():
    print(f"\n{Fore.CYAN}=== AUTONOMOUS PROXY HUNTER ==={Style.RESET_ALL}")
    raw_proxies = fetch_raw_proxies()
    info(f"Berhasil mengumpulkan {len(raw_proxies)} proxy mentah untuk dites.")
    
    if not raw_proxies:
        err("Tidak ada proxy yang bisa dites.")
        sys.exit(1)
        
    # Shuffle agar tidak selalu mengetes yang sama
    import random
    random.shuffle(raw_proxies)
    
    # Batasi pengetesan agar tidak terlalu lama (misalnya max 300)
    test_batch = raw_proxies[:500]
    
    working_proxies = []
    
    warn(f"Memulai tes konkurensi pada {len(test_batch)} proxy ke {TEST_URL} ...")
    start_time = time.time()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        # Submit all tasks
        future_to_proxy = {executor.submit(test_proxy, p): p for p in test_batch}
        
        # As they complete
        for future in concurrent.futures.as_completed(future_to_proxy):
            res = future.result()
            if res:
                working_proxies.append(res)
                ok(f"Lolos Tes: {res} ({len(working_proxies)}/{MAX_PROXIES_NEEDED})")
                
                # Stop if we have enough
                if len(working_proxies) >= MAX_PROXIES_NEEDED:
                    info("Target jumlah proxy terpenuhi. Membatalkan antrean tes lainnya...")
                    # Hack to cancel pending tasks in python 3.8+
                    for f in future_to_proxy:
                        f.cancel()
                    break
                    
    elapsed = time.time() - start_time
    print()
    
    if not working_proxies:
        err(f"Gagal menemukan proxy yang bisa menembus IDX setelah tes selama {elapsed:.1f} detik.")
        sys.exit(1)
        
    ok(f"Selesai! {len(working_proxies)} proxy valid ditemukan ({elapsed:.1f} detik).")
    
    # ── Simpan dan Inject Pengaturan ──
    proxy_file_path = os.path.join(sys_path, "config", "proxies.txt")
    os.makedirs(os.path.dirname(proxy_file_path), exist_ok=True)
    
    with open(proxy_file_path, "w", encoding="utf-8") as f:
        for p in working_proxies:
            f.write(f"{p}\n")
            
    info(f"Daftar proxy ditulis ke {proxy_file_path}")
    
    # Enable proxy setting dynamically in settings.py
    settings_path = os.path.join(sys_path, "config", "settings.py")
    if os.path.exists(settings_path):
        with open(settings_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Replace USE_PROXY = False with USE_PROXY = True
        import re
        content = re.sub(r'USE_PROXY\s*=\s*False', 'USE_PROXY = True', content)
        
        with open(settings_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        info("Mengubah config/settings.py -> USE_PROXY = True terbang! 🚀")

if __name__ == "__main__":
    main()
