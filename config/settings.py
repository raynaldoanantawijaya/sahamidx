import os

# Target keywords untuk deteksi data
TARGET_KEYWORDS = ['harga', 'price', 'emas', 'gold', 'saham', 'stock', 'data', 'nilai', 'items', 'list', 'crypto', 'coin', 'btc', 'bitcoin', 'volume', 'marketcap']

# Mode debugging dan Browser
HEADLESS = False       # Set True untuk production / mode server
TIMEOUT = 60000        # Timeout standar (ms) untuk Playwright
SAVE_HAR = True        # Simpan file HAR untuk debugging manual
HAR_DIR = 'har'

# Sesi & Context
SAVE_SESSION = True
SESSION_DIR = 'sessions'

# Proxy
USE_PROXY = False
PROXY_LIST_FILE = 'config/proxies.txt'
ROTATE_PROXY_EVERY = 10  # Ganti proxy tiap X request

# User-agent
USER_AGENT_ROTATE = True
USER_AGENT_FILE = 'config/user_agents.txt'

# Anti-detection
# Info: Gunakan Patchright jika didukung. Untuk default Playwright, kita pakai script masking di anti_detect.py
USE_PATCHRIGHT = False
PATCHRIGHT_PATH = ''

# Layanan fallback & LLM
USE_WEB_UNLOCKER = False
WEB_UNLOCKER_API_KEY = 'your_key_here'
UNBROWSE_PATH = ''

USE_LLM_PARSER = True
OPENROUTER_API_KEY = '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'

# Logging
LOG_DIR = 'logs'
LOG_FILE = 'scrape.log'
LOG_LEVEL = 'INFO'     # DEBUG, INFO, WARNING, ERROR

# Folder Hasil
RESULT_DIR = 'hasil_scrape'

def ensure_dirs():
    """Memastikan semua direktori yang dibutuhkan tersedia."""
    # Gunakan absolute path atau relative terhadap root project
    base_dir = os.path.dirname(os.path.dirname(__file__))
    for d in [HAR_DIR, SESSION_DIR, LOG_DIR, RESULT_DIR, 'config']:
        path = os.path.join(base_dir, d)
        if not os.path.exists(path):
            os.makedirs(path)

ensure_dirs()
