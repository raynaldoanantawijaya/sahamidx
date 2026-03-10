import logging

logger = logging.getLogger(__name__)

def apply_stealth_to_page(page):
    """
    Penerapan Advanced Stealth (Playwright-Stealth).
    Diterapkan langung pada instance Page (bukan context).
    Berguna untuk bypass ekstrim Cloudflare Turnstile / DataDome.
    """
    try:
        from playwright_stealth import stealth_sync
        logger.debug("Menerapkan playwright-stealth ke Page...")
        stealth_sync(page)
    except ImportError:
        logger.warning("Library 'playwright-stealth' belum terinstall. Menggunakan injeksi manual...")
        script = """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        if (!window.chrome) { window.chrome = { runtime: {} }; }
        Object.defineProperty(navigator, 'plugins', {
            get: () => [{0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format", enabledPlugin: Plugin}, description: "Portable Document Format", filename: "internal-pdf-viewer", length: 1, name: "Chrome PDF Plugin"}]
        });
        const getParameter = WebGLRenderingContext.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.apply(this, arguments);
        };
        """
        page.add_init_script(script)

def apply_stealth(context):
    """
    (Deprecated) Fallback untuk kompatibilitas ke script lama 
    yang masih memanggil stealth ke context layer.
    """
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")


def detect_captcha(html_content):
    """
    Pengecekan simpel mendeteksi keberadaan elemen CAPTCHA populer dari HTML.
    Bisa diekstensi untuk memanggil 2captcha API jika perlu (fallback module).
    """
    if not html_content:
        return False
        
    html_lower = html_content.lower()
    
    indicators = [
        'g-recaptcha',
        'hcaptcha',
        'cf-turnstile',
        'arkose',
        'funcaptcha'
    ]
    
    for indicator in indicators:
        if indicator in html_lower:
            logger.warning(f"Terindikasi adanya CAPTCHA di Halaman!: '{indicator}'")
            return indicator
            
    return False
