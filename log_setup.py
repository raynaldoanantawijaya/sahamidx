import logging
import colorlog
import os
import sys

# Tambahkan path ke sys agar modul config bisa dibaca dengan baik jika dipanggil dari luar root
sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.append(sys_path)
    
from config import settings

def setup_logging():
    """Konfigurasi logging dengan file dan console (berwarna)."""
    # Pastikan direktori log ada
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    log_file_path = os.path.join(settings.LOG_DIR, settings.LOG_FILE)
    
    # Konversi string log level dari settings ke object logging
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Hapus handler yang mungkin sudah ada (mencegah duplikasi log)
    if root_logger.handlers:
        root_logger.handlers.clear()

    # Console Handler dengan warna
    console_handler = colorlog.StreamHandler()
    console_handler.setLevel(level)
    color_formatter = colorlog.ColoredFormatter(
        '%(log_color)s%(asctime)s [%(levelname)s] %(module)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        log_colors={
            'DEBUG':    'cyan',
            'INFO':     'green',
            'WARNING':  'yellow',
            'ERROR':    'red',
            'CRITICAL': 'red,bg_white',
        }
    )
    console_handler.setFormatter(color_formatter)
    
    # File Handler
    file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(module)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Supress library logs except warning/error
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    
    return root_logger

# Initialize saat modul diload
logger = setup_logging()
