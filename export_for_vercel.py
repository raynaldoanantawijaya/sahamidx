"""
export_for_vercel.py
=====================
Buat file JSON statis yang akan disertakan ke dalam kode
saat deploy ke Vercel. Jalankan ini setiap kali data diperbarui
sebelum git push / vercel deploy.

Output: api/data/*.json (files statis yang ikut ke-deploy)
"""
import json
import glob
import os
import shutil
import time

DATA_DIR = "hasil_scrape"
OUT_DIR  = os.path.join("api", "data")
os.makedirs(OUT_DIR, exist_ok=True)

def latest(pattern):
    files = glob.glob(os.path.join(DATA_DIR, pattern))
    return max(files, key=os.path.getmtime) if files else None

exports = {
    "stocks.json":     latest("pluang_all_stocks_*.json"),
    "gold_g24.json":   latest("galeri24_co_id_network_capture_*.json"),
    "gold_he.json":    latest("harga_emas_org_network_capture_*.json"),
    "currencies.json": latest("tradingeconomics_currencies_*.json"),
}

for out_name, src in exports.items():
    out_path = os.path.join(OUT_DIR, out_name)
    if src and os.path.exists(src):
        shutil.copy2(src, out_path)
        size = round(os.path.getsize(out_path) / 1024, 1)
        print(f"✓ {out_name} <- {os.path.basename(src)} ({size} KB)")
    else:
        # Buat file kosong agar import tidak error
        with open(out_path, "w") as f:
            json.dump({}, f)
        print(f"⚠ {out_name} - sumber tidak ditemukan (file kosong dibuat)")

print(f"\nSelesai! {len(exports)} file disalin ke {OUT_DIR}/")
print("Sekarang jalankan: vercel deploy")
