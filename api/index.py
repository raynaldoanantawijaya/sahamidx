"""
api/index.py
=============
Vercel Serverless Flask App — Versi Vercel dari api_server.py

Perbedaan dari api_server.py (versi lokal):
  - Data dibaca dari file JSON statis di api/data/ (bukan hasil_scrape/)
    → Jalankan export_for_vercel.py sebelum deploy untuk mengisi folder ini
  - Word-to-PDF menggunakan ilovepdf OFFICIAL API (bukan Playwright)
    → Daftar di https://developer.ilovepdf.com dan set env ILOVEPDF_PUBLIC_KEY
  - Tidak ada endpoint /api/refresh/* (scraper tidak bisa jalan di Vercel)
  - Semua file PDF temp disimpan ke /tmp (satu-satunya direktori yang bisa ditulis)

Environment Variables yang harus diset di Vercel:
  ILOVEPDF_PUBLIC_KEY   - Public key dari developer.ilovepdf.com (gratis)
  ILOVEPDF_SECRET_KEY   - Secret key dari developer.ilovepdf.com
"""
from flask import Flask, jsonify, request, abort, send_file
from werkzeug.utils import secure_filename
import json
import os
import tempfile
import requests as req_lib

app = Flask(__name__)

# ─── Konfigurasi ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")   # File JSON statis yang sudah di-export

ILOVEPDF_PUBLIC_KEY = os.environ.get("ILOVEPDF_PUBLIC_KEY", "")
ILOVEPDF_SECRET_KEY = os.environ.get("ILOVEPDF_SECRET_KEY", "")

ALLOWED_EXTENSIONS = {"doc", "docx"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_data(filename: str) -> dict:
    """Muat file JSON statis dari api/data/."""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─── CORS ─────────────────────────────────────────────────────────────────────

@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp

@app.route("/api/<path:path>", methods=["OPTIONS"])
def options(path):
    return jsonify({}), 200


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({
        "name": "Auto Scraper API (Vercel)",
        "version": "1.0.0",
        "endpoints": [
            "/api/status",
            "/api/stocks",
            "/api/stocks/<symbol>",
            "/api/gold",
            "/api/gold/<source>",
            "/api/currencies",
            "POST /api/convert/word-to-pdf"
        ]
    })


@app.route("/api/status")
def status():
    stocks  = load_data("stocks.json")
    gold_g  = load_data("gold_g24.json")
    curr    = load_data("currencies.json")
    return jsonify({
        "status": "online",
        "data": {
            "stocks":     {"available": bool(stocks), "total": len(stocks.get("stocks", {}))},
            "gold":       {"available": bool(gold_g)},
            "currencies": {"available": bool(curr), "total": curr.get("metadata", {}).get("total_currency_pairs", 0)}
        }
    })


# ── Stocks ────────────────────────────────────────────────────────────────────

@app.route("/api/stocks")
def get_stocks():
    data = load_data("stocks.json")
    if not data:
        abort(503, description="Data belum tersedia. Deploy ulang setelah menjalankan export_for_vercel.py")

    stocks = data.get("stocks", {})

    search = request.args.get("search", "").upper()
    if search:
        stocks = {k: v for k, v in stocks.items()
                  if search in k or search in v.get("name", "").upper()}

    direction = request.args.get("direction", "").upper()
    if direction in ("GREEN", "RED"):
        stocks = {k: v for k, v in stocks.items() if v.get("direction") == direction}

    sort_by    = request.args.get("sort", "")
    order_desc = request.args.get("order", "asc") == "desc"
    if sort_by == "change":
        stocks = dict(sorted(stocks.items(), key=lambda x: x[1].get("percentageChange", 0), reverse=order_desc))
    elif sort_by == "price":
        stocks = dict(sorted(stocks.items(), key=lambda x: x[1].get("currentPrice") or 0, reverse=order_desc))

    try:
        limit = int(request.args.get("limit", 0))
        if limit > 0:
            stocks = dict(list(stocks.items())[:limit])
    except ValueError:
        pass

    return jsonify({"status": "ok", "count": len(stocks), "stocks": stocks})


@app.route("/api/stocks/<symbol>")
def get_stock(symbol):
    data  = load_data("stocks.json")
    stock = data.get("stocks", {}).get(symbol.upper())
    if not stock:
        abort(404, description=f"Saham '{symbol}' tidak ditemukan.")
    return jsonify({"status": "ok", "data": stock})


# ── Gold ──────────────────────────────────────────────────────────────────────

@app.route("/api/gold")
def get_gold():
    result = {}
    for src, fname in [("galeri24", "gold_g24.json"), ("harga_emas_org", "gold_he.json")]:
        d = load_data(fname)
        prices = d.get("data", {}).get("structured_gold_prices", {})
        if prices:
            result[src] = {"source": src, "prices": prices}
    if not result:
        abort(503, description="Data emas belum tersedia.")
    return jsonify({"status": "ok", "data": result})


@app.route("/api/gold/<source>")
def get_gold_source(source):
    fname_map = {"galeri24": "gold_g24.json", "harga_emas_org": "gold_he.json"}
    fname = fname_map.get(source.lower())
    if not fname:
        abort(404, description=f"Source tidak dikenal: {source}")
    d = load_data(fname)
    prices = d.get("data", {}).get("structured_gold_prices", {})
    if not prices:
        abort(503, description="Data tidak tersedia.")
    provider = request.args.get("provider", "").upper()
    if provider:
        prices = {k: v for k, v in prices.items() if provider in k.upper()}
    return jsonify({"status": "ok", "count": len(prices), "prices": prices})


# ── Currencies ────────────────────────────────────────────────────────────────

@app.route("/api/currencies")
def get_currencies():
    data = load_data("currencies.json")
    if not data:
        abort(503, description="Data currencies belum tersedia.")
    return jsonify({"status": "ok",
                    "metadata": data.get("metadata", {}),
                    "currencies": data.get("currencies", {})})


# ── Word to PDF (via ilovepdf Official API) ───────────────────────────────────

def ilovepdf_convert(file_bytes: bytes, filename: str) -> bytes:
    """
    Konversi Word → PDF menggunakan ilovepdf Official REST API.
    Dokumen: https://developer.ilovepdf.com/docs/api-reference

    Alur:
      1. POST /start/officepdf → dapat task_id + server
      2. POST /upload          → upload file
      3. POST /process         → proses konversi
      4. GET  /download        → unduh PDF
    """
    if not ILOVEPDF_PUBLIC_KEY or not ILOVEPDF_SECRET_KEY:
        raise RuntimeError("Set env var ILOVEPDF_PUBLIC_KEY dan ILOVEPDF_SECRET_KEY di Vercel!")

    base = "https://api.ilovepdf.com/v1"
    auth = (ILOVEPDF_PUBLIC_KEY, ILOVEPDF_SECRET_KEY)

    # 1. Start task
    r = req_lib.post(f"{base}/start/officepdf", auth=auth, timeout=15)
    r.raise_for_status()
    task_data  = r.json()
    task_id    = task_data["task"]
    server_url = f"https://{task_data['server']}/v1"

    # 2. Upload file
    r = req_lib.post(
        f"{server_url}/upload",
        auth=auth,
        data={"task": task_id},
        files={"file": (filename, file_bytes)},
        timeout=30
    )
    r.raise_for_status()
    server_filename = r.json()["server_filename"]

    # 3. Process
    r = req_lib.post(
        f"{server_url}/process",
        auth=auth,
        json={
            "task": task_id,
            "tool": "officepdf",
            "files": [{"server_filename": server_filename, "filename": filename}]
        },
        timeout=30
    )
    r.raise_for_status()

    # 4. Download
    r = req_lib.get(f"{server_url}/download/{task_id}", auth=auth, timeout=30)
    r.raise_for_status()
    return r.content


@app.route("/api/convert/word-to-pdf", methods=["POST"])
def convert_pdf():
    """
    Upload .doc/.docx → download PDF hasil konversi.

    Request : multipart/form-data, field 'file'
    Response: application/pdf
    """
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "Field 'file' tidak ditemukan."}), 400

    file = request.files["file"]
    if not file.filename or not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Format tidak didukung. Gunakan .doc atau .docx"}), 415

    safe_name   = secure_filename(file.filename)
    file_bytes  = file.read()
    pdf_name    = safe_name.rsplit(".", 1)[0] + ".pdf"

    try:
        pdf_bytes = ilovepdf_convert(file_bytes, safe_name)
    except RuntimeError as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Konversi gagal: {e}"}), 500

    # Simpan ke /tmp (satu-satunya folder yang bisa ditulis di Vercel)
    tmp_path = os.path.join(tempfile.gettempdir(), pdf_name)
    with open(tmp_path, "wb") as f:
        f.write(pdf_bytes)

    return send_file(
        tmp_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_name
    )


# ─── Error Handlers ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"status": "error", "code": 404, "message": str(e)}), 404

@app.errorhandler(503)
def unavailable(e):
    return jsonify({"status": "error", "code": 503, "message": str(e)}), 503


# Vercel memanggil `app` secara langsung (WSGI)
# Tidak perlu `if __name__ == "__main__": app.run(...)`
