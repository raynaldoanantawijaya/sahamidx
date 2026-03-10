import os
import sys
import json
import time
import requests
from urllib.parse import urlparse

# Initialize project dan logger
import init_project
init_project.init_project()
import log_setup

import logging
logger = logging.getLogger(__name__)

from config import settings
from modules import direct_request, network_capture, analysis, js_extractor, interaction, decryption, fallback, anti_detect
from modules.proxy_manager import proxy_manager

def main(url, category=""):
    logger.info(f"=== Memulai Auto Web Scraper (6-Layer Intelligence Engine) untuk: {url} ===")
    
    # Tentukan proksi jika digunakan
    req_proxy = proxy_manager.get_proxy_for_requests()
    playwright_proxy = proxy_manager.get_proxy_for_playwright()
    
    if req_proxy:
        logger.info(f"Menggunakan Proxy: {playwright_proxy.get('server')}")

    # =========================================================
    # LAYER 1: SSR / Next.js Data Parser (Pluang / Kompas)
    # =========================================================
    try:
        logger.info("-> [Layer 1] Memeriksa SSR State Murni (Next.js / Nuxt)...")
        html_req = direct_request.request(url, timeout=10, proxies=req_proxy, return_raw=True)
        if isinstance(html_req, str):
            inline_data = analysis.extract_inline_json(html_req, settings.TARGET_KEYWORDS)
            if inline_data:
                logger.info("Layer 1 Sukses: API Data berhasil ditarik dari script SSR State!")
                return save_data(inline_data, url, "layer1_ssr_parser", category)
    except Exception as e:
        logger.debug(f"Layer 1 SSR Parser dilewati: {e}")

    # =========================================================
    # LAYER 2: Network Interception & API Harvesting
    # =========================================================
    capture_result = None
    failed_api_endpoints = []
    
    try:
        logger.info("-> [Layer 2] Memulai Network Capture & Parsing Murni...")
        capture_result = network_capture.capture(
            url, 
            stealth_config=anti_detect.apply_stealth,
            proxies=playwright_proxy
        )
        
        # Cek CAPTCHA
        captcha_type = anti_detect.detect_captcha(capture_result.html_content)
        if captcha_type:
            logger.warning(f"=== CAPTCHA TERDETEKSI ({captcha_type}) ===")
            fallback.solve_captcha_external(capture_result.html_content, url, captcha_type)
            
        # Ekstrak JSON API yang didapat oleh Network Capture
        data = analysis.find_json(capture_result, settings.TARGET_KEYWORDS)
        
        # Kumpulkan API endpoints yang gagal/terblokir (HTTP 401/403/Kosong) untuk Layer 4
        for resp in capture_result.responses:
            r_url = resp.get("url", "")
            r_status = resp.get("status", 200)
            if r_status in [401, 403, 400] or (r_status == 200 and not resp.get("body")):
                if "api" in r_url.lower() or any(kw.lower() in r_url.lower() for kw in settings.TARGET_KEYWORDS):
                    failed_api_endpoints.append(r_url)
                    
    except Exception as e:
        logger.error(f"Error pada Layer 2/Network Capture: {e}")

    # =========================================================
    # LAYER 3: Smart DOM HTML Parser (Azarug / DrakorKita)
    # =========================================================
    # Menjalankan Layer 3 DULUAN jika HTML sangat jelas berstruktur WordPress/Streaming
    # Ini mencegah scraper tertipu oleh JSON tracking API yang kebetulan mengandung kata 'data'.
    dom_data = None
    if capture_result and capture_result.html_content:
        logger.info("-> [Layer 3] Mengeksekusi Smart DOM HTML Parser (Heuristik WordPress/Streaming)...")
        dom_data = analysis.smart_dom_extract(capture_result.html_content)
        
        # Jika temuan DOM kuat (banyak artikel atau video), langsung jadikan prioritas utama!
        if dom_data and (len(dom_data.get("articles", [])) > 2 or dom_data.get("video_embeds")):
            logger.info(f"Layer 3 Sukses: Berhasil membedah DOM! Menemukan {len(dom_data.get('articles', []))} artikel yang jauh lebih relevan.")
            return save_data(dom_data, url, "layer3_smart_dom", category)

    # =========================================================
    # JIKA DOM KOSONG, KEMBALI EVALUASI HASIL LAYER 2 (Network Intercept)
    # =========================================================
    if data and not data.get("smart_dom"): 
        logger.info("-> Mengambil hasil JSON dari Network Interception (Layer 2)...")
        return save_data(data, url, "layer2_network_capture", category)

    # =========================================================
    # LAYER 4: Playwright Native Context Fetch (IDX Technique)
    # =========================================================
    if failed_api_endpoints:
        try:
            logger.info(f"-> [Layer 4] Terdeteksi {len(failed_api_endpoints)} Endpoint Terblokir (Token/Auth).")
            logger.info("Mengeksekusi Native Context Fetch dari dalam browser Playwright...")
            
            unique_eps = list(set(failed_api_endpoints))[:5]
            native_data = network_capture.native_browser_fetch(url, unique_eps, stealth_config=anti_detect.apply_stealth, proxies=playwright_proxy)
            
            if native_data:
                logger.info("Layer 4 Sukses: Native Fetch berhasil menembus proteksi token API!")
                return save_data(native_data, url, "layer4_native_fetch", category)
        except Exception as e:
            logger.error(f"Error pada Layer 4: {e}")

    # =========================================================
    # LAYER 5: WordPress Admin-AJAX Extraction (Zelda Technique)
    # =========================================================
    if capture_result and capture_result.html_content:
        try:
            html = capture_result.html_content
            if "wp-admin/admin-ajax.php" in html or "wp-content" in html:
                logger.info("-> [Layer 5] Indikasi Tema WordPress Terdeteksi. Menembak AJAX...")
                # Ekstrak post_id dari berbagai macam cara biasa
                import re
                pid_match = re.search(r'"post_id":"?(\d+)"?', html) or re.search(r'data-post="?(\d+)"?', html) or re.search(r'postid-(\d+)', html)
                
                if pid_match:
                    post_id = pid_match.group(1)
                    parsed_url = urlparse(url)
                    ajax_url = f"{parsed_url.scheme}://{parsed_url.netloc}/wp-admin/admin-ajax.php"
                    
                    logger.info(f"Mencoba injeksi payload AJAX ke {ajax_url} dengan Post ID {post_id}...")
                    payload = {"action": "dp_drakor_get_eps", "post_id": post_id} # Contoh Zelda Action 1
                    res1 = requests.post(ajax_url, data=payload, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                    
                    payload2 = {"action": "halos_get_player", "post_id": post_id} # Contoh Zelda Action 2
                    res2 = requests.post(ajax_url, data=payload2, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)

                    combined_ajax_data = {}
                    if res1.status_code == 200 and "{" in res1.text:
                        combined_ajax_data["ajax_response_1"] = res1.text
                    if res2.status_code == 200 and "{" in res2.text:
                        combined_ajax_data["ajax_response_2"] = res2.text
                        
                    if combined_ajax_data:
                        logger.info("Layer 5 Sukses: Berhasil memaksa server WordPress memuntahkan iframe rahasia via AJAX!")
                        return save_data(combined_ajax_data, url, "layer5_wp_ajax", category)
        except Exception as e:
            logger.debug(f"Layer 5 WP AJAX dilewati: {e}")

    # =========================================================
    # LAYER 5.5: Ultimate AI Parser (OpenRouter LLM)
    # =========================================================
    if capture_result and capture_result.html_content and settings.USE_LLM_PARSER:
        logger.info("-> [Layer 5.5] Menjalankan AI LLM Parser. Melempar teks HTML mentah ke OpenRouter...")
        # Kirim text ke OpenRouter untuk melihat apakah AI bisa memaksakan format JSON
        llm_data = analysis.ai_llm_extract(capture_result.html_content)
        if llm_data:
            logger.info("Layer 5.5 Sukses: AI LLM berhasil menyusun data tersetruktur dari HTML!")
            return save_data(llm_data, url, "layer5_5_ai_llm", category)

    # =========================================================
    # LAYER 6: Decryption & External Fallback
    # =========================================================
    if capture_result:
        try:
            if analysis.is_encrypted(capture_result):
                logger.info("-> [Layer 6] Terindikasi Enkripsi Extrim, Mencoba Dekripsi Basic...")
                decrypted_data = decryption.try_decrypt(capture_result, url)
                if decrypted_data:
                    return save_data(decrypted_data, url, "layer6_decrypted", category)
        except Exception as e:
            logger.error(f"Error Dekripsi Layer 6: {e}")

    try:
        if settings.USE_WEB_UNLOCKER:
            logger.info("-> [Layer 6] Semua fallback lokal buntu, menggunakan Layanan Web Unlocker Eksternal...")
            data = fallback.use_web_unlocker(url)
            if data:
                return save_data(data, url, "layer6_fallback_unlocker", category)
        else:
            logger.info("-> [Layer 6] Web Unlocker dimatikan. Melewati bypass eksternal.")
    except Exception as e:
        logger.error(f"Error Unlocker Layer 6: {e}")

    logger.error("=== Kegagalan Total Scraping: Tidak menemukan satupun pola data bermakna. ===")
    logger.info("Data yang tertangkap dari browser telah disimpan di folder har/ (jika aktif).")
    return None

def save_data(data, source_url, method, category=""):
    """
    Menyimpan data JSON ke folder hasil_scrape/ dan
    memberikan output jelas ke terminal.
    """
    import json, os, time
    from urllib.parse import urlparse
    
    try:
        out_dir = "hasil_scrape"
        if category:
            out_dir = os.path.join(out_dir, category)
        os.makedirs(out_dir, exist_ok=True)
        
        domain = urlparse(source_url).netloc.replace('.', '_')
        if not domain:
            domain = "unknown"
            
        filename = os.path.join(out_dir, f"{domain}_{method}_{int(time.time())}.json")
        
        final_data = {
            "metadata": {
                "source": source_url,
                "extraction_method": method,
                "timestamp": int(time.time()),
                "auto_scraper_version": "v2.0 (6-Layer Intel)"
            },
            "data": data
        }
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=2, ensure_ascii=False)
            
        size = round(os.path.getsize(filename) / 1024, 1)
        logger.info(f"\\n> OUTPUT TERSIMPAN: {os.path.abspath(filename)}")
        logger.info(f"> METODE: {method} | UKURAN: {size} KB")
        return final_data
        
    except Exception as e:
        logger.error(f"Gagal menyimpan data ke JSON: {e}")
        return data

if __name__ == "__main__":
    import sys, argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Target URL to scrape")
    parser.add_argument("--category", help="Category subfolder to save output to", default="")
    args = parser.parse_args()
    main(args.url, args.category)
