import logging
import json
import re
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def is_json_valid(text):
    try:
        json.loads(text)
        return True
    except (ValueError, TypeError):
        return False

def smart_dom_extract(html_content: str) -> dict:
    """
    Kecerdasan untuk mengekstrak data dari DOM secara heuristik.
    Berguna ketika website (seperti WordPress stream, blog) me-render
    langsung datanya di HTML tanpa API terpisah.
    """
    if not html_content:
        return {}
        
    soup = BeautifulSoup(html_content, "html.parser")
    found_data = {}
    
    # Heuristik 1: Artikel / Item (WordPress / Rebahin / Blog themes)
    articles = soup.find_all(["article", "div"], class_=lambda c: c and any(kw in str(c).lower() for kw in ["item", "post", "card", "entry", "box"]))
    if articles:
        extracted_articles = []
        for index, item in enumerate(articles[:100]): # Limit to prevent memory bloat
            a_tag = item.find("a", href=True)
            title_tag = item.find(["h1", "h2", "h3", "h4", "strong"])
            
            title = title_tag.text.strip() if title_tag else ""
            if not title and a_tag:
                title = a_tag.get("title", "") or a_tag.text.strip()
                
            href = a_tag["href"] if a_tag else ""
            
            if title or href:
                extracted_articles.append({
                    "id": index + 1,
                    "judul": title,
                    "url": href,
                    "excerpt": item.text.strip()[:100].replace("\\n", " ")
                })
        if extracted_articles:
            found_data["articles"] = extracted_articles
            
    # Heuristik 2: Video Embeds (Iframe)
    iframes = soup.find_all("iframe")
    if iframes:
        videos = []
        for v in iframes:
            src = v.get("src", "")
            if src and "youtube" not in src and "ads" not in src:
                videos.append(src)
        if videos:
            found_data["video_embeds"] = videos
            
    return found_data

def ai_llm_extract(html_content: str) -> dict:
    """
    Layer 5.5: Ultimate AI Fallback. 
    Mengirimkan teks mentah dari HTML ke OpenRouter LLM 
    untuk dipaksa disusun menjadi struktur JSON.
    """
    import os
    import sys
    sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if sys_path not in os.sys.path:
        os.sys.path.append(sys_path)
    from config import settings
    
    if not settings.USE_LLM_PARSER or not settings.OPENROUTER_API_KEY:
        logger.warning("LLM Parser nonaktif atau API Key tidak disetel.")
        return {}
        
    if not html_content:
        return {}

    logger.info("Mempersiapkan teks HTML untuk dikirim ke AI LLM (OpenRouter)...")
    
    # Cleaning the HTML to strictly readable text to save tokens
    soup = BeautifulSoup(html_content, "html.parser")
    for script_or_style in soup(["script", "style", "noscript", "svg"]):
        script_or_style.extract()
    
    clean_text = soup.get_text(separator=' ', strip=True)
    # Potong maksimal 15.000 karakter agar tidak melebihi konteks standar model kecil
    clean_text = clean_text[:15000]
    
    if len(clean_text) < 100:
        logger.warning("Teks HTML terlalu sedikit untuk dianalisa AI.")
        return {}
        
    prompt = f"""
    You are an expert data scraper. I will give you the raw text extracted from a webpage.
    Your ABSOLUTE ONLY JOB is to find any structured data (like list of items, movies, prices, stocks, articles, or tables) inside the text, and output it as a STRICT VALID JSON ARRAY of OBJECTS.
    DO NOT output ANY markdown formatting, DO NOT output ```json, JUST output the raw JSON array starting with [ and ending with ].
    If you cannot find any meaningful structured data, output [].
    
    RAW TEXT TO ANALYZE:
    {clean_text}
    """
    
    import requests
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/rayna/auto-scraper",
        "X-Title": "Auto Scraper CLI"
    }
    
    # Gunakan model gratis terkuat di OpenRouter (Llama 3.3 70B)
    # untuk menghindari 402 Payment Required dan 404 Not Found
    model_name = "meta-llama/llama-3.3-70b-instruct:free"
    
    payload = {
        "model": model_name, 
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1
    }
    
    try:
        logger.info(f"Mengirim instruksi ke OpenRouter ({model_name})... mohon tunggu...")
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=45)
        
        if response.status_code == 200:
            res_json = response.json()
            ai_text = res_json['choices'][0]['message']['content'].strip()
            
            # Sanitasi sedikit barangkali AI masih ngotot ngasih markdown
            if ai_text.startswith("```json"):
                ai_text = ai_text[7:]
            if ai_text.startswith("```"):
                ai_text = ai_text[3:]
            if ai_text.endswith("```"):
                ai_text = ai_text[:-3]
                
            ai_text = ai_text.strip()
            
            extracted_json = json.loads(ai_text)
            
            if extracted_json and len(extracted_json) > 0:
                logger.info(f"AI LLM Sukses! Berhasil menyusun {len(extracted_json)} baris data dari teks kacau.")
                return {"ai_structured_data": extracted_json}
            else:
                logger.warning("AI LLM merespon dengan JSON kosong (tidak menemukan struktur).")
                return {}
        else:
            logger.error(f"Gagal memanggil OpenRouter API: {response.text}")
            return {}
            
    except Exception as e:
        logger.error(f"Error saat memproses LLM Extraction: {e}")
        return {}

def find_json(capture_result, target_keywords):
    """
    Analisis dari body XHR/Fetch/Document dan WebSocket untuk menemukan data berharga
    berdasarkan target_keywords.
    Mengembalikan data valid JSON jika ditemukan.
    """
    logger.info("Memulai analisis data yang ditangkap...")
    found_data = {}
    
    # 1. Analisis Response XHR/Fetch/Document
    for resp in capture_result.responses:
        body = resp.get("body", "")
        url = resp.get("url", "")
        
        # Coba parse langsung jika content-type adalah JSON
        if "application/json" in resp.get("content_type", "") or is_json_valid(body):
            try:
                data = json.loads(body)
                # Evaluasi keyword
                body_lower = body.lower()
                url_lower = url.lower()
                for kw in target_keywords:
                    if kw.lower() in body_lower or kw.lower() in url_lower:
                        found_data[url] = data
                        logger.info(f"Target keyword '{kw}' ditemukan di endpoint: {url}")
                        break
            except Exception:
                pass

    # 2. Analisis Inline JSON dari HTML
    inline_results = extract_inline_json(capture_result.html_content, target_keywords)
    if inline_results:
        found_data["inline_html_data"] = inline_results
        logger.info("Berhasil mengekstrak inline JSON dari HTML.")

    # 3. Analisis Websocket Messages
    ws_data = []
    for msg in capture_result.websocket_messages:
        text = msg.get("data", "")
        if isinstance(text, str) and is_json_valid(text):
            for kw in target_keywords:
                if kw.lower() in text.lower():
                    try:
                        ws_data.append(json.loads(text))
                        break
                    except Exception:
                        pass
    if ws_data:
        found_data["websocket_data"] = ws_data
        logger.info("Berhasil mengekstrak data dari koneksi WebSocket.")

    # 4. Fallback Table Extraction
    table_results = extract_html_tables(capture_result.html_content, target_keywords)
    if table_results:
        found_data["html_tables_data"] = table_results
        logger.info("Berhasil mengekstrak data dari tabel HTML statis.")

    # 5. Smart Structuring (Emas / Gold Prices spesifik)
    structured_gold = structure_gold_data(found_data)
    if structured_gold:
        found_data["structured_gold_prices"] = structured_gold
        logger.info("Berhasil memformat dan menyusun ulang Harga Emas secara rapi!")

    # 6. Smart Structuring (Saham / Stock Prices spesifik)
    structured_stocks = structure_stock_data(found_data)
    if structured_stocks:
        found_data["structured_stocks"] = structured_stocks
        logger.info(f"Berhasil memformat data Saham! ({len(structured_stocks)} ticker ditemukan)")

    if found_data:
        return found_data
        
    # 7. Fallback DOM Parsing (Layer 5) 
    # Jika tidak ada JSON atau Tabel, coba bedah DOM secara pintar
    # Berguna untuk WordPress / Streaming / Berita yang di-render hardcoded
    logger.info("Tidak ada JSON ditemukan. Mencoba Smart DOM Extraction heuristik...")
    dom_data = smart_dom_extract(capture_result.html_content)
    if dom_data:
        logger.info(f"Berhasil mengekstrak {len(dom_data.get('articles', []))} entri artikel/item dari DOM HTML.")
        return {"smart_dom": dom_data}

    return None

def structure_gold_data(raw_found_data):
    """
    Kecerdasan untuk menyortir data mentah yang ditemukan (dari network/html)
    menjadi direktori terstruktur seperti:
    {
      "Antam": {"1 Gram": "3.162.000", ...},
      "Pegadaian": {...}
    }
    """
    structured = {}
    
    providers = ['Antam', 'UBS', 'Pegadaian', 'Global', 'Spot']
    
    # Fungsi pembantu untuk memproses list bersarang (seperti HTML Table Data)
    def process_table_rows(rows):
        # Cari row yang berisi header satuan dan provider
        provider_indices = {}
        satuan_idx = -1
        
        for r_idx, row in enumerate(rows):
            if not isinstance(row, list): continue
            
            # Deteksi Header (Pastikan hanya pada baris pertama/kedua)
            if r_idx < 3 and satuan_idx == -1:
                for c_idx, col in enumerate(row):
                    if not isinstance(col, str): continue
                    col_lower = col.lower()
                    
                    # Cek apakah kolom ini untuk Satuan (Gram/Kg)
                    if 'satuan' in col_lower or 'gram' in col_lower or 'berat' in col_lower:
                        satuan_idx = c_idx
                        
                    # Cek apakah kolom ini untuk Provider Emas
                    for p in providers:
                        if p.lower() in col_lower:
                            provider_indices[p] = c_idx
                            if p not in structured:
                                structured[p] = {}
                            
            # Jika Header sudah ditemukan, proses baris berikutnya (angka gram & harganya)
            if provider_indices and satuan_idx != -1 and r_idx >= 1:
                # Coba proses baris ini jika isinya angka
                try:
                    satuan_str = row[satuan_idx].strip()
                    # Pastikan satuan ini adalah angka (misal '1', '0.5', '1000') gram
                    if satuan_str.replace('.', '').replace(',', '').isdigit() or ('.' in satuan_str and satuan_str.replace('.', '').isdigit()):
                        for p, p_idx in provider_indices.items():
                            if p_idx < len(row) and p_idx != satuan_idx:
                                harga = row[p_idx].strip()
                                # Jika harga berupa angka dengan titik/koma (format rupiah)
                                if any(char.isdigit() for char in harga):
                                    structured[p][f"{satuan_str} Gram"] = harga
                except Exception:
                    pass

    # 1. Telusuri data dari tabel HTML
    if 'html_tables_data' in raw_found_data:
        for table in raw_found_data['html_tables_data']:
            if 'data' in table and isinstance(table['data'], list):
                process_table_rows(table['data'])
                
    # 2. Parsing cerdas untuk Nuxt/Vue state arrays (seperti Galeri24)
    # Di SSR Nuxt, state berbentuk array referensi indeks untuk kompresi size:
    # Contoh array: ["Reactive", 1, {}, ..., {"vendorName": 15, "sellingPrice": 7}, ..., "19000"]
    if 'inline_html_data' in raw_found_data:
        for script in raw_found_data['inline_html_data']:
            if script.get('type') == 'inline_script' and isinstance(script.get('content'), list):
                vue_arr = script['content']
                # Kalau index pertama adalah string "Reactive" (atau sekadar ada object dg vendorName)
                if len(vue_arr) > 0 and (vue_arr[0] == "Reactive" or any(isinstance(i, dict) and 'vendorName' in i for i in vue_arr[:50])):
                    for item in vue_arr:
                        if isinstance(item, dict) and 'vendorName' in item and 'denomination' in item and 'sellingPrice' in item:
                            try:
                                v_name_idx = item['vendorName']
                                denom_idx = item['denomination']
                                sell_idx = item['sellingPrice']
                                
                                # Dereference nilai index
                                v_name = vue_arr[v_name_idx]
                                denom = vue_arr[denom_idx]
                                sell_price = vue_arr[sell_idx]
                                
                                if isinstance(v_name, str) and isinstance(denom, str) and isinstance(sell_price, str):
                                    if v_name not in structured:
                                        structured[v_name] = {}
                                        
                                    structured[v_name][f"{denom} Gram"] = sell_price
                            except (IndexError, TypeError):
                                pass

    return structured if structured else None

def structure_stock_data(raw_found_data):
    """
    Mengekstrak data saham terstruktur dari data SSR halaman Pluang.
    Menghasilkan dictionary berindeks simbol saham (ticker), misalnya:
    {
        "NVDA": {
            "name": "Nvidia Corp",
            "symbol": "NVDA",
            "currentPrice": 189.82,
            "currentPriceDisplay": "$189,82",
            "percentageChange": 1.02,
            "percentageDisplay": "+1,02%",
            "marketCap": "$4,6T",
            "lastClosingPrice": 187.9,
            "arrowIcon": "GREEN"
        },
        ...
    }
    """
    structured = {}
    
    def walk_inline(content_obj):
        """Cari secara rekursif objek assets dari pageProps."""
        if not isinstance(content_obj, dict):
            return
        
        # Target: pageProps -> data -> assetCategories -> assetCategoryData -> assets -> tileInfo
        page_props = content_obj.get('props', {}).get('pageProps', {})
        if not page_props:
            # Coba akses data langsung (kadang nested berbeda)
            page_props = content_obj.get('pageProps', {})
        
        explore_data = page_props.get('data', {})
        asset_categories = explore_data.get('assetCategories', [])
        
        for cat in asset_categories:
            for sub_cat_data in cat.get('assetCategoryData', []):
                for asset in sub_cat_data.get('assets', []):
                    try:
                        tile = asset.get('tileInfo', {})
                        display = asset.get('display', {})
                        price_info = display.get('lastPriceAndPercentageChange', {})
                        cap_info = display.get('marketCap', {})
                        
                        symbol = tile.get('symbol', '')
                        if not symbol:
                            continue
                        
                        structured[symbol] = {
                            "name": tile.get('name', ''),
                            "symbol": symbol,
                            "assetId": tile.get('assetId'),
                            "securityType": tile.get('securityType', ''),
                            "isTradable": tile.get('isTradable', False),
                            "currentPrice": price_info.get('currentPrice'),
                            "currentPriceDisplay": price_info.get('currentPriceDisplay', ''),
                            "percentageChange": round(price_info.get('percentageChange', 0), 4),
                            "percentageDisplay": price_info.get('percentageDisplay', ''),
                            "direction": price_info.get('arrowIcon', ''),
                            "lastClosingPrice": price_info.get('lastClosingPrice'),
                            "dividendAmount": price_info.get('dividendAmount', 0),
                            "marketCap": cap_info.get('value', ''),
                            "sparkLine": tile.get('sparkLine', '')
                        }
                    except Exception:
                        pass
    
    # Cari di semua inline script content
    for script in raw_found_data.get('inline_html_data', []):
        if script.get('type') in ('inline_script', 'ld+json'):
            walk_inline(script.get('content', {}))
    
    return structured if structured else None

def extract_inline_json(html_content, target_keywords):
    """
    Mengekstrak JSON LD, script tag contents, dan data attributes dari HTML.
    """
    results = []
    if not html_content:
        return results
        
    soup = BeautifulSoup(html_content, 'lxml')
    
    # a. Cari semua tag script dengan application/ld+json
    ld_json_scripts = soup.find_all('script', type='application/ld+json')
    for script in ld_json_scripts:
        if script.string:
            try:
                data = json.loads(script.string)
                results.append({"type": "ld+json", "content": data})
            except Exception:
                pass

    # b. Cari script yang mungkin berisi objek JSON global
    # Misalnya windows.__INITIAL_STATE__ = {...}
    scripts = soup.find_all('script')
    for script in scripts:
        if script.string and ('{' in script.string or '[' in script.string):
            # Coba ekstrak yang berbentuk objek murni
            match = re.search(r'^\s*({.*}|\[.*\])\s*$', script.string, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    results.append({"type": "inline_script", "content": data})
                except Exception:
                    pass
            else:
                # Coba cari object yang dideklarasi sebagai variabel
                # JSON.parse("...") atau var x = {...}
                # Fallback ke pencarian regex dasar
                json_matches = re.findall(r'(?<=(=|:)\s)\s*({[^{}]*({[^{}]*}[^{}]*)*})\s*(?=;|\n|$)', script.string)
                for m in json_matches:
                    try:
                        # m[1] adalah grup yang mewakili JSON object
                        if is_json_valid(m[1]):
                            data = json.loads(m[1])
                            results.append({"type": "variable_injection", "content": data})
                    except Exception:
                        pass

    # c. Ekstrak data- attributes yang berisi JSON
    elements_with_data = soup.find_all(lambda tag: any(attr.startswith('data-') for attr in tag.attrs))
    for el in elements_with_data:
        for attr, value in el.attrs.items():
            if attr.startswith('data-') and is_json_valid(value):
                try:
                    data = json.loads(value)
                    results.append({"type": "data_attribute", "tag": el.name, "attr": attr, "content": data})
                except Exception:
                    pass

    return results

def extract_html_tables(html_content, target_keywords):
    """
    Fallback: Mengekstrak tabel HTML statis menjadi format JSON dictionary.
    Digunakan jika website masih menggunakan Server Side Rendering tradisional
    dan tidak mengekspos API JSON.
    """
    results = []
    if not html_content:
        return results
        
    soup = BeautifulSoup(html_content, 'lxml')
    tables = soup.find_all('table')
    
    for idx, table in enumerate(tables):
        # Cek apakah tabel ini relevan (mengandung keyword)
        table_text = table.get_text(separator=' ', strip=True).lower()
        is_relevant = any(kw.lower() in table_text for kw in target_keywords)
        
        if is_relevant:
            table_data = []
            headers = []
            
            # Ekstrak Header
            th_elements = table.find_all('th')
            if th_elements:
                headers = [th.get_text(strip=True) for th in th_elements]
                
            # Ekstrak Baris
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all(['td', 'th'])
                if not cols or (th_elements and cols == th_elements):
                    continue
                    
                row_data = [col.get_text(strip=True) for col in cols]
                
                # Jika ada headers yang pas ukurannya, buat jadi dict
                if headers and len(headers) == len(row_data):
                    table_data.append(dict(zip(headers, row_data)))
                else:
                    table_data.append(row_data)
                    
            if table_data:
                results.append({
                    "table_index": idx,
                    "data": table_data
                })
                
    return results

def is_encrypted(capture_result):
    """
    Memeriksa jika ada response yang memiliki ciri-ciri terenkripsi.
    (Content-Type JSON tapi isinya tidak valid JSON, string base64 / hex yang random).
    """
    for resp in capture_result.responses:
        body = resp.get("body", "")
        # Kalau header mengindikasikan JSON, tapi isinya ngaco
        if "application/json" in resp.get("content_type", ""):
            if not is_json_valid(body) and len(body) > 10:
                # Biasanya base64 / random characters, atau ada parameter "data" tapi isinya string yg panjang
                if bool(re.match('^[A-Za-z0-9+/=]+$', body.strip())) or bool(re.match('^[0-9a-fA-F]+$', body.strip())):
                    return True
            # Cek jika format valid JSON tapi isinya seperti {"data": "AwdawDawdawd..."}
            if is_json_valid(body):
                try:
                    data = json.loads(body)
                    if isinstance(data, dict) and len(data.keys()) == 1:
                        val = str(list(data.values())[0])
                        # Kalo typenya string panjang
                        if len(val) > 30 and ' ' not in val:
                            return True
                except Exception:
                    pass
    return False
