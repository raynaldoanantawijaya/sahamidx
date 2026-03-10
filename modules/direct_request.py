import logging
import requests
import json
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

COMMON_API_ENDPOINTS = [
    '/api/data',
    '/api/v1/data',
    '/data.json',
    '/graphql',
    '/api/items',
    '/wp-json/wp/v2/posts',
    '/api/values',
    '/openapi.json',
    '/api/search',
    '/api/products'
]

def try_common_endpoints(base_url, timeout=10, proxies=None, headers=None):
    """
    Mencoba mengakses langsung endpoint umum yang biasa digunakan untuk API.
    Akan mengembalikan dictionary JSON jika ditemukan.
    """
    logger.info(f"Mencoba Direct Request ke endpoint umum untuk URL: {base_url}")
    
    # Hapus trailing slash jika ada
    if base_url.endswith('/'):
        base_url = base_url[:-1]
        
    if not headers:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*'
        }

    # Cek apakah base_url itu sendiri adalah endpoint data
    base_data = request(base_url, timeout, proxies, headers)
    if base_data:
        return base_data

    for endpoint in COMMON_API_ENDPOINTS:
        target_url = f"{base_url}{endpoint}"
        logger.debug(f"Direct request ke: {target_url}")
        
        data = request(target_url, timeout, proxies, headers)
        if data:
            logger.info(f"Berhasil mendapatkan data dari {target_url}")
            return data
            
        # Coba variasi dengan parameter id atau pagination
        target_url_page = f"{target_url}?page=1"
        data_page = request(target_url_page, timeout, proxies, headers)
        if data_page:
            logger.info(f"Berhasil mendapatkan data (pagination) dari {target_url_page}")
            return data_page
            
    logger.info("Direct Request tidak menemukan data JSON pada endpoint umum.")
    return None

def request(url, timeout=10, proxies=None, headers=None):
    """
    Melakukan HTTP GET request dan mengekstrak JSON.
    """
    try:
        response = requests.get(url, timeout=timeout, proxies=proxies, headers=headers, verify=False)
        # Jika berhasil, coba parse sebagai JSON
        if response.status_code == 200:
            if 'application/json' in response.headers.get('Content-Type', '').lower():
                return response.json()
            else:
                # Terkadang response JSON tidak memiliki Content-Type yang benar
                try:
                    return response.json()
                except ValueError:
                    pass
    except requests.exceptions.RequestException as e:
        logger.debug(f"Gagal akses {url}: {e}")
    except ValueError as e:
        logger.debug(f"Bukan format JSON yang valid pada {url}")
        
    return None
