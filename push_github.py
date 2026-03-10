import os
import sys
import shutil
import time
import subprocess
from urllib.parse import urlparse

try:
    from colorama import init, Fore, Back, Style
    init(autoreset=True)
except ImportError:
    class _Noop:
        def getattr(self, _): pass
    Fore = Back = Style = _Noop()

def ok(msg): print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {msg}")
def err(msg): print(f"  {Fore.RED}✗{Style.RESET_ALL} {msg}")
def info(msg): print(f"  {Fore.CYAN}ℹ{Style.RESET_ALL} {msg}")
def warn(msg): print(f"  {Fore.YELLOW}⚠{Style.RESET_ALL} {msg}")

def run_git_command(args, cwd, hide_output=True):
    """Menjalankan perintah git dan mengembalikan stdout (str) dan success (bool)."""
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode == 0:
            return res.stdout.strip(), True
        else:
            return res.stderr.strip(), False
    except Exception as e:
        return str(e), False

def is_valid_github_url(url: str) -> bool:
    """Validasi dasar URL GitHub"""
    if "github.com" not in url.lower():
        return False
    # Bersihkan URL jika diisi utuh
    url = url.strip()
    return True

def format_github_url(url: str) -> str:
    """Pastikan URL berakhiran .git dan dibersihkan dari trailing slashes"""
    url = url.strip().rstrip("/")
    if not url.endswith(".git"):
         url += ".git"
    return url

def push_file_to_github(source_file_path: str, repo_url: str, target_filename: str):
    """
    Meng-clone repo, copy file scrape ke repo, commit, lalu push back.
    Fungsi ini dijalankan di temp folder agar tidak merusak repo scraper saat ini.
    """
    
    if not os.path.exists(source_file_path):
        err(f"File sumber tidak ditemukan: {source_file_path}")
        return False
        
    if not is_valid_github_url(repo_url):
        err("URL tidak valid. Pastikan itu adalah URL repository GitHub (misal: https://github.com/user/repo).")
        return False
        
    clean_repo_url = format_github_url(repo_url)
    
    # ── 1. SETUP TEMP WOKRSPACE ──
    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp_git_push")
    if os.path.exists(tmp_dir):
        # Bersihkan folder temp sisa push sebelumnya jika ada
        shutil.rmtree(tmp_dir, ignore_errors=True)
        time.sleep(0.5)
        
    os.makedirs(tmp_dir, exist_ok=True)
    
    try:
        info(f"Mempersiapkan repository target: {Fore.CYAN}{clean_repo_url}{Style.RESET_ALL}")
        
        # ── 2. CLONE (SHALLOW) ──
        warn("Melakukan git clone (depth=1) untuk kecepatan...")
        # Note: 'repo' adalah nama subfolder dalam .tmp_git_push
        stdout, success = run_git_command(["clone", "--depth", "1", clean_repo_url, "repo"], tmp_dir)
        if not success:
            err(f"Gagal clone repository. Pastikan URL benar dan Anda punya akses (berupa public repo atau auth tersetting).\nGit Error: {stdout}")
            return False
            
        repo_dir = os.path.join(tmp_dir, "repo")
        
        # Cari branch utama (bisa main atau master)
        branch_out, success = run_git_command(["branch", "--show-current"], repo_dir)
        current_branch = branch_out if success and branch_out else "main"
        
        # ── 3. COPY FILE ──
        target_file_path = os.path.join(repo_dir, target_filename)
        info(f"Menyalin hasil scrape ke: {Fore.CYAN}{target_filename}{Style.RESET_ALL}")
        shutil.copy2(source_file_path, target_file_path)
        
        # Pastikan JSON file-nya sudah tercopy dengan baik
        if not os.path.exists(target_file_path):
             err("Gagal memindah file JSON ke dalam folder repository.")
             return False
             
        # ── 3.5 VERCEL BOILERPLATE INJECTION ──
        vercel_json_path = os.path.join(repo_dir, "vercel.json")
        has_vercel_json_already = os.path.exists(vercel_json_path)
        if not has_vercel_json_already:
             warn("Membuat vercel.json untuk mengizinkan akses CORS lintas domain...")
             import json
             vercel_config = {
               "headers": [
                 {
                   "source": "/(.*)",
                   "headers": [
                     { "key": "Access-Control-Allow-Origin", "value": "*" },
                     { "key": "Access-Control-Allow-Methods", "value": "GET, OPTIONS" }
                   ]
                 }
               ]
             }
             with open(vercel_json_path, "w", encoding="utf-8") as vf:
                 json.dump(vercel_config, vf, indent=2)
             
        # ── 3.6 STANDALONE REPO INJECTION ──
        warn("Menginjeksi kode scraper & GitHub Actions...")
        import glob
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Copy requirements.txt
        req_src = os.path.join(base_dir, "requirements.txt")
        if os.path.exists(req_src):
            shutil.copy2(req_src, os.path.join(repo_dir, "requirements.txt"))
            
        # Copy all python files in root
        for py_file in glob.glob(os.path.join(base_dir, "*.py")):
            if not os.path.basename(py_file).startswith("test_") and not os.path.basename(py_file).startswith("."):
                shutil.copy2(py_file, os.path.join(repo_dir, os.path.basename(py_file)))
        
        # Copy essential subdirectories (config, modules, api)
        essential_dirs = ["config", "modules", "api"]
        for subdir in essential_dirs:
            src_dir = os.path.join(base_dir, subdir)
            dst_dir = os.path.join(repo_dir, subdir)
            if os.path.isdir(src_dir):
                shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
                
        # Determine category based on path substring
        category = "all"
        path_lower = source_file_path.lower()
        if "saham" in path_lower: category = "saham"
        elif "emas" in path_lower: category = "emas"
        elif "crypto" in path_lower: category = "crypto"
        elif "berita" in path_lower: category = "berita"
        elif "forex" in path_lower: category = "forex"
        
        # Create .github/workflows/auto_scrape.yml
        workflows_dir = os.path.join(repo_dir, ".github", "workflows")
        os.makedirs(workflows_dir, exist_ok=True)
        yml_path = os.path.join(workflows_dir, "auto_scrape.yml")
        
        yml_content = f"""name: Standalone Scraper ({category})

on:
  schedule:
    - cron: '0 */4 * * *'
  workflow_dispatch:

jobs:
  scrape_and_commit:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'
          cache: 'pip'
          
      - name: Install dependencies
        run: pip install -r requirements.txt
        
      - name: Install Playwright
        run: playwright install chromium --with-deps
        
      - name: Run Scraper
        run: python menu.py --{category}
        
      - name: Commit & Push New JSON Data
        run: |
          git config --global user.name 'github-actions[bot]'
          git config --global user.email '41898282+github-actions[bot]@users.noreply.github.com'
          
          # Ambil hasil JSON terbaru dari folder 
          latest_json=$(ls -t hasil_scrape/{category}/*.json | head -1 || true)
          if [ -n "$latest_json" ] && [ -f "$latest_json" ]; then
             echo "Updating endpoint with $latest_json"
             cp "$latest_json" "{target_filename}"
          else
             echo "Tidak ada file JSON baru dihasilkan."
          fi
          
          git add {target_filename}
          git diff --quiet && git diff --staged --quiet || (git commit -m "bot: Auto-update {category} API endpoint" && git push)
"""
        with open(yml_path, "w", encoding="utf-8") as f:
            f.write(yml_content)

        # ── 4. COMMIT & PUSH ──
        warn("Menyiapkan commit...")
        # Add EVERYTHING since we inject standalone codebase
        _, success = run_git_command(["add", "."], repo_dir)
        if not success:
            err("Gagal git add pada file-file standalone repo.")
            return False
            
        # Check jika ada perubahan
        status_out, _ = run_git_command(["status", "--porcelain"], repo_dir)
        if not status_out.strip():
            ok(f"File {target_filename} sudah up-to-date di repository. Tidak ada push yang diperlukan.")
            return True
            
        # Commit
        commit_msg = f"Auto-update API endpoint: {target_filename} [{int(time.time())}]"
        stdout, success = run_git_command(["commit", "-m", commit_msg], repo_dir)
        if not success:
            err(f"Gagal melakukan commit.\nGit Error: {stdout}")
            return False
            
        # Push!
        info(f"Melakukan push otomatis ke branch '{current_branch}'...")
        stdout, success = run_git_command(["push", "origin", current_branch], repo_dir)
        
        if success:
            ok("Push berhasil!")
            print(f"\n  {Fore.GREEN}Endpoint JSON Anda sudah siap di GitHub!{Style.RESET_ALL}")
            
            # Buat asumsi URL raw.githubusercontent.com jika memungkinkan
            try:
                # https://github.com/Username/Repo.git -> Username/Repo
                path_parts = urlparse(clean_repo_url).path.strip("/").replace(".git", "")
                raw_url = f"https://raw.githubusercontent.com/{path_parts}/refs/heads/{current_branch}/{target_filename}"
                print(f"  {Fore.CYAN}🔗 Raw/API Link : {raw_url}{Style.RESET_ALL}")
                print(f"  {Fore.CYAN}🔗 GitHub Link : https://github.com/{path_parts}/blob/{current_branch}/{target_filename}{Style.RESET_ALL}\n")
            except Exception:
                 pass # Fallback jika parsing raw link gagal
                 
            return True
        else:
            err(f"Gagal push ke repository target.\nSila verifikasi kredensial git (username/token) di sistem Anda.\nGit Error: {stdout}")
            return False
            
    finally:
        # ── 5. TEMPORARY FOLDER CLEANUP ──
        try:
             # Paksa lepas read-only files (seperti .git objects) sebelum rmtree di Windows
             def remove_readonly(func, path, excinfo):
                 import os, stat
                 os.chmod(path, stat.S_IWRITE)
                 func(path)
             shutil.rmtree(tmp_dir, onerror=remove_readonly)
        except Exception as e:
             # Folder temp mungkin error di lock oleh windows OS sebentar, abaikan saja
             pass
