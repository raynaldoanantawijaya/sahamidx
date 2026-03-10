"""
convert_word_to_pdf.py
=======================
Konversi Word (.doc/.docx) → PDF secara LOKAL menggunakan docx2pdf.
Menggunakan Microsoft Word yang sudah terpasang di Windows — CEPAT (~2-3 detik).

Tidak memerlukan browser, Playwright, atau koneksi internet.

Digunakan oleh api_server.py endpoint /api/convert/word-to-pdf
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger("word_to_pdf")


def convert_word_to_pdf(input_path: str, output_dir: str = None) -> str:
    """
    Konversi file Word ke PDF secara lokal via docx2pdf.

    Args:
        input_path: Path absolut ke file .docx atau .doc
        output_dir: Direktori output PDF (default: sama dengan direktori input)

    Returns:
        Path absolut ke file PDF hasil konversi

    Raises:
        FileNotFoundError: File input tidak ada
        RuntimeError: Konversi gagal
    """
    import docx2pdf

    input_path = os.path.abspath(input_path)
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"File tidak ditemukan: {input_path}")

    if output_dir is None:
        output_dir = os.path.dirname(input_path)
    os.makedirs(output_dir, exist_ok=True)

    filename = Path(input_path).stem
    pdf_path = os.path.join(output_dir, f"{filename}.pdf")

    logger.info(f"Konversi lokal: {input_path} → {pdf_path}")

    try:
        docx2pdf.convert(input_path, pdf_path)
    except Exception as e:
        raise RuntimeError(f"Konversi gagal: {e}") from e

    if not os.path.exists(pdf_path):
        raise RuntimeError("File PDF tidak terbentuk setelah konversi.")

    size = os.path.getsize(pdf_path)
    logger.info(f"✓ PDF berhasil: {pdf_path} ({size} bytes)")
    return pdf_path


if __name__ == "__main__":
    import sys
    import time
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python convert_word_to_pdf.py <path_to_docx>")
        sys.exit(1)

    t0 = time.time()
    result = convert_word_to_pdf(sys.argv[1], output_dir="hasil_scrape/pdf_output")
    elapsed = round(time.time() - t0, 2)
    print(f"\n✅ Selesai dalam {elapsed}s! PDF disimpan: {result}")
