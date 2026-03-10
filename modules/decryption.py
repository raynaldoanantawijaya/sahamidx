import logging
import base64
import re
import json

logger = logging.getLogger(__name__)

def try_decrypt(capture_result, target_url):
    """
    Mencoba melakukan basic dekripsi (Base64 / pola standar JSON yang dikaburkan)
    Atau mem-bypass enkripsi tertentu jika algoritma diketahui dan disimpan di js_extractor.
    Mengembalikan data valid (JSON) jika berhasil, None jika gagal.
    """
    logger.info("Mencoba melakukan Reverse Engineering enkripsi payload...")
    
    decrypted_results = {}
    
    for resp in capture_result.responses:
        body = resp.get("body", "").strip()
        url = resp.get("url", "")
        
        # Hanya coba decode jika body tidak kosong dan bentuknya bukan JSON yang sah
        if len(body) > 10 and not (body.startswith('{') or body.startswith('[')):
            # Coba Base64 Decode
            try:
                # Pad jika perlu
                padding_needed = len(body) % 4
                if padding_needed:
                    body_padded = body + '=' * (4 - padding_needed)
                else:
                    body_padded = body
                    
                decoded_bytes = base64.b64decode(body_padded, validate=False)
                decoded_str = decoded_bytes.decode('utf-8')
                
                # Cek apakah string hasil decode adalah JSON sah
                json_data = json.loads(decoded_str)
                decrypted_results[url] = json_data
                logger.debug(f"Berhasil decode base64 payload dari: {url}")
                continue  # Lanjut ke response berikutnya
            except Exception:
                pass
                
            # Disini dapat ditambahkan algoritma spesifik misal AES dengan predefined key,
            # menggunakan pycryptodome. Karena sifatnya sangat dinamis, kita biarkan logic
            # ini sebagai placeholder untuk algoritma yang diketahui.
    
    return decrypted_results if decrypted_results else None

import execjs

def execute_js_function(js_logic, function_name, *args):
    """
    Mengeksekusi sebuah snippet fungsi (contoh algoritma pembuat `sign` / `token`)
    menggunakan PyExecJS.
    """
    try:
        context = execjs.compile(js_logic)
        result = context.call(function_name, *args)
        return result
    except Exception as e:
        logger.error(f"Gagal execute JS Function '{function_name}': {e}")
        return None
