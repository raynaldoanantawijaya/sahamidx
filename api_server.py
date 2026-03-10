"""
api_server.py
=============
REST API Server untuk menyajikan data hasil scraping sebagai endpoint JSON.
Dapat langsung digunakan oleh website/frontend Anda.

Endpoints:
  GET  /api/status                  - Status server & ringkasan data
  GET  /api/stocks                  - Semua saham US (Pluang)
  GET  /api/stocks/<symbol>         - Detail saham by ticker (AAPL, TSLA, dll)
  GET  /api/gold                    - Semua harga emas (Galeri24 / harga-emas.org)
  GET  /api/gold/<provider>         - Harga emas by provider (ANTAM, UBS, dll)
  GET  /api/crypto                  - Data crypto (CoinMarketCap)
  POST /api/convert/word-to-pdf     - Upload .docx → download PDF (via ilovepdf.com)
  POST /api/refresh/stocks          - Jalankan ulang scraper saham (update data)
  GET  /api/refresh/status          - Status refresh terakhir

Contoh penggunaan dari frontend (JavaScript fetch):
  const res = await fetch('http://localhost:5000/api/stocks/AAPL')
  const stock = await res.json()
  console.log(stock.currentPrice)  // 264.58

Jalankan server:
  python api_server.py
"""
from flask import Flask, jsonify, request, abort, send_file
from flask.wrappers import Response
from functools import lru_cache
from werkzeug.utils import secure_filename
import json
import os
import glob
import threading
import subprocess
import tempfile
import time
import logging
from datetime import datetime

# ─── Konfigurasi ─────────────────────────────────────────────────────────────
app = Flask(__name__)

DATA_DIR = "hasil_scrape"
CACHE_TTL_SECONDS = 300  # Cache data di memori selama 5 menit

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("api_server")

refresh_status = {
    "is_running": False,
    "last_run": None,
    "last_result": None
}

# ─── Helpers: Muat file JSON terbaru ─────────────────────────────────────────

def get_latest_file(pattern: str) -> str | None:
    """Cari file JSON terbaru yang cocok dengan pola glob."""
    files = glob.glob(os.path.join(DATA_DIR, pattern))
    if not files:
        return None
    return max(files, key=os.path.getmtime)

_cache = {}

def load_json_cached(pattern: str) -> dict | None:
    """Load file JSON terbaru dengan in-memory cache (TTL 5 menit)."""
    now = time.time()
    if pattern in _cache:
        data, ts = _cache[pattern]
        if now - ts < CACHE_TTL_SECONDS:
            return data

    filepath = get_latest_file(pattern)
    if not filepath:
        return None
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        _cache[pattern] = (data, now)
        logger.info(f"Loaded: {filepath}")
        return data
    except Exception as e:
        logger.error(f"Gagal load {filepath}: {e}")
        return None

# ─── CORS Header (agar bisa diakses dari domain website lain) ─────────────────

@app.after_request
def add_cors_headers(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp

@app.route("/api/<path:path>", methods=["OPTIONS"])
def handle_options(path):
    return jsonify({}), 200

# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({
        "name": "Auto Scraper API",
        "version": "1.0.0",
        "docs": "/api/status",
        "endpoints": [
            "/api/status",
            "/api/stocks",
            "/api/stocks/<symbol>",
            "/api/gold",
            "/api/gold/<provider>",
            "/api/crypto",
            "POST /api/convert/word-to-pdf",
            "/api/refresh/stocks",
            "/api/refresh/status"
        ]
    })


@app.route("/api/status")
def status():
    """Ringkasan data yang tersedia."""
    stocks_data = load_json_cached("pluang_all_stocks_*.json")
    gold_g24    = load_json_cached("galeri24_co_id_network_capture_*.json")
    gold_he     = load_json_cached("harga_emas_org_network_capture_*.json")
    crypto_data = load_json_cached("coinmarketcap_com_network_capture_*.json")

    return jsonify({
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "data_summary": {
            "stocks": {
                "available": stocks_data is not None,
                "total": len(stocks_data.get("stocks", {})) if stocks_data else 0,
                "source": "pluang.com/explore/us-market/stocks"
            },
            "gold": {
                "available": (gold_g24 is not None) or (gold_he is not None),
                "sources": {
                    "galeri24": gold_g24 is not None,
                    "harga_emas_org": gold_he is not None
                }
            },
            "crypto": {
                "available": crypto_data is not None,
                "source": "coinmarketcap.com"
            }
        }
    })


# ─── Stocks Endpoints ─────────────────────────────────────────────────────────

@app.route("/api/stocks")
def get_all_stocks():
    """
    Semua data saham AS (639 ticker).
    Query params:
      ?search=NVDA       - filter by ticker substring
      ?direction=GREEN   - filter hanya saham naik (GREEN) atau turun (RED)
      ?sort=change       - urutkan by persentase perubahan
      ?order=desc        - order asc/desc (default asc)
      ?limit=50          - batasi jumlah hasil
    """
    data = load_json_cached("pluang_all_stocks_*.json")
    if not data:
        abort(503, description="Data saham belum tersedia. Jalankan scraper terlebih dahulu.")

    stocks: dict = data.get("stocks", {})

    # Filter by search (symbol atau name)
    search = request.args.get("search", "").upper()
    if search:
        stocks = {k: v for k, v in stocks.items()
                  if search in k.upper() or search in v.get("name", "").upper()}

    # Filter by direction
    direction = request.args.get("direction", "").upper()
    if direction in ("GREEN", "RED"):
        stocks = {k: v for k, v in stocks.items() if v.get("direction") == direction}

    # Sort
    sort_by = request.args.get("sort", "")
    order_desc = request.args.get("order", "asc").lower() == "desc"
    if sort_by == "change":
        stocks = dict(sorted(stocks.items(),
                             key=lambda x: x[1].get("percentageChange", 0),
                             reverse=order_desc))
    elif sort_by == "price":
        stocks = dict(sorted(stocks.items(),
                             key=lambda x: x[1].get("currentPrice") or 0,
                             reverse=order_desc))
    elif sort_by == "name":
        stocks = dict(sorted(stocks.items(),
                             key=lambda x: x[1].get("name", ""),
                             reverse=order_desc))

    # Limit
    try:
        limit = int(request.args.get("limit", 0))
        if limit > 0:
            stocks = dict(list(stocks.items())[:limit])
    except ValueError:
        pass

    return jsonify({
        "status": "ok",
        "count": len(stocks),
        "source": data.get("metadata", {}),
        "stocks": stocks
    })


@app.route("/api/stocks/<symbol>")
def get_stock(symbol: str):
    """Detail satu saham berdasarkan ticker symbol (contoh: /api/stocks/AAPL)."""
    data = load_json_cached("pluang_all_stocks_*.json")
    if not data:
        abort(503, description="Data saham belum tersedia.")

    symbol = symbol.upper()
    stock = data.get("stocks", {}).get(symbol)
    if not stock:
        abort(404, description=f"Saham '{symbol}' tidak ditemukan.")

    return jsonify({"status": "ok", "data": stock})


# ─── IDX Endpoints ────────────────────────────────────────────────────────────

@app.route("/api/idx/stocks")
def get_idx_stocks():
    """Seluruh data saham IDX beserta Ringkasan Perdagangan."""
    data = load_json_cached("idx_combined_*.json")
    if not data:
        abort(503, description="Data IDX belum tersedia. Jalankan scraper IDX terlebih dahulu.")
        
    stocks = data.get("stocks", {})
    
    # Filter
    board = request.args.get("board", "").title()
    if board:
        stocks = {k: v for k, v in stocks.items() if v.get("Papan_Pencatatan") == board}
        
    return jsonify({
        "status": "ok",
        "count": len(stocks),
        "source": data.get("metadata", {}),
        "data": stocks
    })

@app.route("/api/idx/brokers")
def get_idx_brokers():
    """Seluruh data Ringkasan Broker IDX."""
    data = load_json_cached("idx_combined_*.json")
    if not data:
        abort(503, description="Data IDX belum tersedia.")
        
    brokers = data.get("brokers", [])
    
    return jsonify({
        "status": "ok",
        "count": len(brokers),
        "source": data.get("metadata", {}),
        "data": brokers
    })

# ─── Gold Endpoints ───────────────────────────────────────────────────────────

@app.route("/api/gold")
def get_all_gold():
    """
    Semua data harga emas dari semua provider.
    Menggabungkan data dari Galeri24 dan harga-emas.org.
    """
    result = {}

    g24 = load_json_cached("galeri24_co_id_network_capture_*.json")
    if g24:
        g24_prices = g24.get("data", {}).get("structured_gold_prices", {})
        if g24_prices:
            result["galeri24"] = {
                "source": "galeri24.co.id",
                "prices": g24_prices
            }

    he = load_json_cached("harga_emas_org_network_capture_*.json")
    if he:
        he_prices = he.get("data", {}).get("structured_gold_prices", {})
        if he_prices:
            result["harga_emas_org"] = {
                "source": "harga-emas.org",
                "prices": he_prices
            }

    if not result:
        abort(503, description="Data emas belum tersedia. Jalankan scraper terlebih dahulu.")

    return jsonify({"status": "ok", "data": result})


@app.route("/api/gold/<source>")
def get_gold_by_source(source: str):
    """
    Harga emas dari source tertentu.
    Gunakan: /api/gold/galeri24 atau /api/gold/harga_emas_org
    Query: ?provider=ANTAM (opsional, filter by provider)
    """
    pattern_map = {
        "galeri24": "galeri24_co_id_network_capture_*.json",
        "harga_emas_org": "harga_emas_org_network_capture_*.json",
        "harga-emas": "harga_emas_org_network_capture_*.json",
    }
    pattern = pattern_map.get(source.lower())
    if not pattern:
        abort(404, description=f"Source '{source}' tidak dikenal. Gunakan: galeri24, harga_emas_org")

    data = load_json_cached(pattern)
    if not data:
        abort(503, description=f"Data dari '{source}' belum tersedia.")

    prices = data.get("data", {}).get("structured_gold_prices", {})
    if not prices:
        abort(404, description="Tidak ada structured_gold_prices di file ini.")

    # Filter by provider
    provider = request.args.get("provider", "").upper()
    if provider:
        prices = {k: v for k, v in prices.items() if provider in k.upper()}
        if not prices:
            abort(404, description=f"Provider '{provider}' tidak ditemukan.")

    return jsonify({"status": "ok", "count": len(prices), "prices": prices})


# ─── Crypto Endpoints ─────────────────────────────────────────────────────────

@app.route("/api/crypto")
def get_crypto():
    """
    Data crypto dari CoinMarketCap.
    Mengembalikan semua response API yang berhasil ditangkap dari network capture.
    """
    data = load_json_cached("coinmarketcap_com_network_capture_*.json")
    if not data:
        abort(503, description="Data crypto belum tersedia. Jalankan scraper terlebih dahulu.")

    # Ambil endpoint URL yang berisi data trading
    crypto_data = data.get("data", {})
    # Filter hanya key yang URL API (bukan inline/html)
    api_responses = {k: v for k, v in crypto_data.items()
                     if k.startswith("http") and isinstance(v, dict)}

    return jsonify({
        "status": "ok",
        "metadata": data.get("metadata", {}),
        "api_endpoints_captured": list(api_responses.keys()),
        "total_endpoints": len(api_responses),
        "data": api_responses
    })



# ─── News Endpoint (Kompas.com) ──────────────────────────────────────────────

@app.route("/api/news")
def get_news():
    """
    Berita terbaru dari Kompas.com (7 seksi: Utama, Nasional, Ekonomi, dll).
    Query params:
      ?section=Nasional   - filter by section name
      ?search=ekonomi     - search by keyword dalam judul
      ?limit=20           - batasi jumlah hasil
    Jalankan scrape_kompas_news.py untuk memperbarui data.
    """
    data = load_json_cached("kompas_news_*.json")
    if not data:
        abort(503, description="Data berita belum tersedia. Jalankan scrape_kompas_news.py terlebih dahulu.")

    articles = data.get("articles", [])

    section = request.args.get("section", "").strip()
    if section:
        articles = [a for a in articles if section.lower() in a.get("section", "").lower()]

    search = request.args.get("search", "").lower()
    if search:
        articles = [a for a in articles if search in a.get("judul", "").lower()]

    try:
        limit = int(request.args.get("limit", 0))
        if limit > 0:
            articles = articles[:limit]
    except ValueError:
        pass

    return jsonify({
        "status": "ok",
        "count": len(articles),
        "metadata": data.get("metadata", {}),
        "articles": articles
    })


# ─── Word to PDF Converter Endpoint ─────────────────────────────────────────

ALLOWED_EXTENSIONS = {"doc", "docx"}
PDF_OUTPUT_DIR = os.path.join("hasil_scrape", "pdf_output")
os.makedirs(PDF_OUTPUT_DIR, exist_ok=True)

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/api/convert/word-to-pdf", methods=["POST"])
def convert_word_pdf():
    """
    Upload file Word (.doc/.docx) dan dapatkan PDF hasil konversi.

    Request:
      Content-Type: multipart/form-data
      Field name  : 'file'  (file .doc atau .docx)

    Response:
      Content-Type: application/pdf
      Body        : File PDF yang sudah dikonversi (langsung di-download)

    Contoh curl:
      curl -X POST http://localhost:5000/api/convert/word-to-pdf \\
           -F "file=@dokumen.docx" \\
           -o hasil.pdf

    Contoh JavaScript fetch:
      const form = new FormData()
      form.append('file', fileInput.files[0])
      const res = await fetch('/api/convert/word-to-pdf', { method: 'POST', body: form })
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      // tampilkan atau download blob PDF
    """
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "Tidak ada file yang dikirim. Gunakan form-data dengan field 'file'."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"status": "error", "message": "Filename kosong."}), 400

    if not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Format tidak didukung. Gunakan .doc atau .docx"}), 415

    # Simpan file upload ke direktori sementara
    safe_name = secure_filename(file.filename)
    with tempfile.TemporaryDirectory(prefix="word_upload_") as tmp_dir:
        input_path = os.path.join(tmp_dir, safe_name)
        file.save(input_path)
        logger.info(f"File diterima: {safe_name} ({os.path.getsize(input_path)} bytes)")

        try:
            from convert_word_to_pdf import convert_word_to_pdf
            pdf_path = convert_word_to_pdf(input_path, output_dir=PDF_OUTPUT_DIR)
        except RuntimeError as e:
            logger.error(f"Konversi gagal: {e}")
            return jsonify({"status": "error", "message": f"Konversi gagal: {e}"}), 500
        except Exception as e:
            logger.error(f"Error tidak terduga: {e}")
            return jsonify({"status": "error", "message": "Error internal server."}), 500

    # Kirim file PDF sebagai response download
    pdf_filename = os.path.basename(pdf_path).replace(".docx", ".pdf").replace(".doc", ".pdf")
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_filename
    )


# ─── Refresh Endpoint ─────────────────────────────────────────────────────────

@app.route("/api/refresh/stocks", methods=["POST"])
def refresh_stocks():
    """
    Trigger scraping ulang data saham (jalankan scrape_pluang_stocks.py).
    Proses berjalan di background, cek status di /api/refresh/status
    """
    if refresh_status["is_running"]:
        return jsonify({
            "status": "already_running",
            "message": "Scraper sedang berjalan, cek /api/refresh/status"
        }), 409

    def run_scraper():
        refresh_status["is_running"] = True
        refresh_status["last_run"] = datetime.now().isoformat()
        try:
            result = subprocess.run(
                ["python", "scrape_pluang_stocks.py"],
                capture_output=True, text=True, timeout=300
            )
            refresh_status["last_result"] = {
                "returncode": result.returncode,
                "stdout": result.stdout[-2000:],  # last 2000 chars
                "stderr": result.stderr[-1000:] if result.stderr else None
            }
            # Bust cache setelah update
            _cache.clear()
            logger.info("Stocks refresh selesai, cache di-clear.")
        except Exception as e:
            refresh_status["last_result"] = {"error": str(e)}
        finally:
            refresh_status["is_running"] = False

    thread = threading.Thread(target=run_scraper, daemon=True)
    thread.start()

    return jsonify({
        "status": "started",
        "message": "Scraper berjalan di background. Cek /api/refresh/status untuk progress.",
        "check_url": "/api/refresh/status"
    }), 202


@app.route("/api/refresh/status")
def refresh_status_endpoint():
    """Status scraper refresh terakhir."""
    return jsonify({
        "is_running": refresh_status["is_running"],
        "last_run": refresh_status["last_run"],
        "last_result": refresh_status["last_result"]
    })


# ─── Error Handlers ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"status": "error", "code": 404, "message": str(e)}), 404

@app.errorhandler(503)
def unavailable(e):
    return jsonify({"status": "error", "code": 503, "message": str(e)}), 503


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "="*65)
    print("  🚀 AUTO SCRAPER API SERVER")
    print("="*65)
    print("  Endpoints tersedia:")
    print("    http://localhost:5000/api/status")
    print("    http://localhost:5000/api/stocks")
    print("    http://localhost:5000/api/stocks/AAPL")
    print("    http://localhost:5000/api/gold")
    print("    http://localhost:5000/api/gold/galeri24")
    print("    http://localhost:5000/api/crypto")
    print("    POST http://localhost:5000/api/convert/word-to-pdf  (upload .docx → PDF)")
    print("    POST http://localhost:5000/api/refresh/stocks")
    print("="*65 + "\n")
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB max upload
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
