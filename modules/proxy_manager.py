import os
import random
import logging

# Adjust sys_path
import sys
sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if sys_path not in sys.path:
    sys.path.append(sys_path)
    
from config import settings

logger = logging.getLogger(__name__)

class ProxyManager:
    def __init__(self):
        self.proxies = []
        self.current_index = 0
        self.request_count = 0
        self.load_proxies()

    def load_proxies(self):
        proxy_file = os.path.join(sys_path, settings.PROXY_LIST_FILE)
        if settings.USE_PROXY and os.path.exists(proxy_file):
            try:
                with open(proxy_file, 'r') as f:
                    lines = f.readlines()
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            self.proxies.append(line)
                logger.info(f"Loaded {len(self.proxies)} proxies.")
            except Exception as e:
                logger.error(f"Failed to load proxies: {e}")

    def get_proxy_for_requests(self):
        """Mengembalikan dict proxy format 'requests' lib."""
        if not settings.USE_PROXY or not self.proxies:
            return None
            
        proxy_url = self._get_current()
        return {
            'http': proxy_url,
            'https': proxy_url
        }

    def get_proxy_for_playwright(self):
        """Mengembalikan dict proxy format Playwright."""
        if not settings.USE_PROXY or not self.proxies:
            return None
            
        proxy_url = self._get_current()
        return {
            "server": proxy_url
        }

    def _get_current(self):
        """Mengembalikan satu string URL proxy (http://user:pass@ip:port)"""
        # Rotasi berdasarkan setting ROTATE_PROXY_EVERY
        self.request_count += 1
        if self.request_count >= settings.ROTATE_PROXY_EVERY:
            self.current_index = (self.current_index + 1) % len(self.proxies)
            self.request_count = 0
            logger.debug(f"Rotating proxy to index: {self.current_index}")
            
        return self.proxies[self.current_index]

# Singleton instance export
proxy_manager = ProxyManager()
