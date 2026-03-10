import re
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def extract_from_js(url, html_content=None, proxies=None):
    """
    Ekstrak endpoint API dan nama fungsi dinamis dari file JavaScript di halaman.
    """
    logger.info(f"Memulai ekstraksi JS untuk: {url}")
    endpoints = set()
    js_urls = []
    
    if not html_content:
        try:
            resp = requests.get(url, timeout=15, proxies=proxies, verify=False)
            html_content = resp.text
        except Exception as e:
            logger.error(f"Gagal mengambil HTML dari {url} untuk ekstraksi JS: {e}")
            return list(endpoints)

    soup = BeautifulSoup(html_content, 'lxml')
    scripts = soup.find_all('script')
    
    # 1. Kumpulkan URL javascript eksternal
    for script in scripts:
        src = script.get('src')
        if src:
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                from urllib.parse import urlparse
                parsed = urlparse(url)
                base = f"{parsed.scheme}://{parsed.netloc}"
                src = base + src
            js_urls.append(src)
            
    # 2. Periksa script inline
    for script in scripts:
        if script.string:
            endpoints.update(_find_patterns(script.string))
            
    # 3. Download dan periksa file JS eksternal
    for js_url in set(js_urls): # Gunakan set agar unik
        try:
            logger.debug(f"Mengunduh JS: {js_url}")
            js_resp = requests.get(js_url, timeout=10, proxies=proxies, verify=False)
            if js_resp.status_code == 200:
                endpoints.update(_find_patterns(js_resp.text))
        except Exception as e:
            logger.debug(f"Gagal mengunduh {js_url}: {e}")
            
    logger.info(f"Ditemukan {len(endpoints)} endpoint/pola dari file JS.")
    return list(endpoints)

def _find_patterns(js_text):
    """
    Mencari pola endpoint API dan token logic dalam teks JavaScript menggunakan regex.
    """
    found = set()
    
    # URL absolut API
    abs_urls = re.findall(r'https?://[^"\'\s]+api[^"\'\s]*', js_text)
    for u in abs_urls: found.add(u)
        
    # Pola fetch / axios
    fetch_patterns = re.findall(r'(?:fetch|axios\.(?:get|post|put|delete))\s*\(\s*["\']([^"\']+)["\']', js_text)
    for p in fetch_patterns: found.add(p)
        
    # URL properties (biasanya di objek konfigurasi ajax)
    url_props = re.findall(r'(?:url|endpoint|api)\s*:\s*["\']([^"\']+)["\']', js_text)
    for p in url_props: found.add(p)
        
    # Pola v1, v2 (API versioning standard)
    versioned = re.findall(r'(?<=["\'])/v\d+/[a-zA-Z0-9_\/-]+(?=["\'])', js_text)
    for p in versioned: found.add(p)
        
    # Bersihkan hasil dari false positive (seperti tag html atau spasi kosong)
    cleaned = set()
    for item in found:
        if len(item) > 2 and '<' not in item and '>' not in item:
            # Jika relative url tdk diawali '/', tambahkan placeholder (akan diurus di main)
            cleaned.add(item)
            
    return cleaned
