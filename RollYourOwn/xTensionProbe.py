import csv
import os
import time
import random
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import selenium_stealth
import requests
import questionary

# === Config ===
INPUT_CSV = 'epstein_no_images_pdf_urls.csv'          # Input scraped URLs
OUTPUT_CSV = 'epstein_media_checked_urls.csv'         # Output with media finds
COOKIES_FILE = 'doj_cookies.json'                     # Temp cookie storage
MAX_WORKERS = 5                                       # Reduced for rate limiting
REQUEST_TIMEOUT = 30
BATCH_SIZE = 50                                       # More frequent updates

# --- Full list of all possible extensions ---
ALL_EXTENSIONS = [
    # --- VIDEOS ---
    '.mp4', '.mov', '.webm', '.avi', '.mkv', 
    '.m4v', '.3gp', '.3g2', '.flv', '.f4v', 
    '.wmv', '.asf', '.ogv', '.m2ts', '.mts', 
    '.ts', '.qt', '.mxf', '.vob', '.dv', 
    '.mod', '.tod', '.rm', '.rmvb', '.divx',
    
    # --- AUDIO ---
    '.mp3', '.wav', '.ogg', '.m4a', '.m4b', 
    '.aac', '.opus', '.wma', '.aiff', '.flac', 
    '.amr', '.caf', '.mka', '.mid',
    
    # --- IMAGES ---
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', 
    '.tiff', '.tif', '.webp', '.heic', '.heif', 
    '.ico', '.tga', '.psd'
]

# --- Interactive Extension Selection ---
if __name__ == "__main__":
    _CONTACT = "\033[40;97m @ThatRetiredDude on 𝕏 or MaxwellInternational.ai \033[0m"
    print("Follow the on-screen instructions. Any questions? Contact", _CONTACT)
    # Use questionary to let the user select which extensions to probe
    MEDIA_EXTENSIONS = questionary.checkbox(
        "Select extensions to scan (Space to toggle, Enter to confirm):",
        choices=[
            # High-priority extensions are checked by default
            questionary.Choice(ext, checked=(ext in ['.mp4', '.mov', '.jpg', '.jpeg', '.png', '.mp3']))
            for ext in ALL_EXTENSIONS
        ]
    ).ask()

    if not MEDIA_EXTENSIONS:
        print("No extensions selected. Exiting.")
        exit()

    # --- Worker Count Selection ---
    MAX_WORKERS_str = questionary.text(
        f"Enter number of concurrent workers (threads) [1-50, default: {MAX_WORKERS}]:",
        default=str(MAX_WORKERS)
    ).ask()
    try:
        user_max_workers = int(MAX_WORKERS_str)
        if 1 <= user_max_workers <= 50:
            MAX_WORKERS = user_max_workers
        else:
            print("Invalid number. Using default.")
    except (ValueError, TypeError):
        print("Invalid input. Using default.")
    
    print(f"Using {MAX_WORKERS} workers.")
    print(f"Starting scan for: {', '.join(MEDIA_EXTENSIONS)}")

error_threshold = 10
consecutive_401s = 0

# Load input URLs
if not os.path.exists(INPUT_CSV):
    print(f"Error: {INPUT_CSV} not found!")
    exit()

urls = []
with open(INPUT_CSV, 'r', newline='', encoding='utf-8') as f:
    reader = csv.reader(f)
    next(reader, None)
    for row in reader:
        if row:
            urls.append(row[0].strip())

print(f"Loaded {len(urls)} URLs to probe")

# Load existing output for resume/skip
processed_stems = set()
updates = {}
if os.path.exists(OUTPUT_CSV):
    with open(OUTPUT_CSV, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) >= 3 and row[0]:
                original = row[0]
                stem = original.rsplit('.', 1)[0]
                media_type = row[2].strip()
                if media_type not in ['no_media_yet', 'pdf_or_not_found']:
                    size_bytes = int(row[3]) if len(row) > 3 and row[3].isdigit() else -1
                    updates[stem] = {
                        'actual_url': row[1], 
                        'media_type': media_type,
                        'size_bytes': size_bytes
                    }
                    processed_stems.add(stem)
    print(f"Resumed from existing output: {len(processed_stems)} already processed")

# --- New columns for size and tiny file flag ---
def save_progress():
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['original_url', 'actual_url', 'media_type', 'size_bytes', 'is_tiny'])
        for original_url in urls:
            stem = original_url.rsplit('.', 1)[0]
            if stem in updates:
                # Add size and tiny flag to the row
                size = updates[stem].get('size_bytes', -1)
                is_tiny = size < (1024 * 100) if size != -1 else False
                writer.writerow([
                    original_url, 
                    updates[stem]['actual_url'], 
                    updates[stem]['media_type'],
                    size,
                    is_tiny
                ])
            else:
                writer.writerow([original_url, original_url, 'no_media_yet', -1, False])
    print(f"Progress saved to {OUTPUT_CSV}: {len(updates)} media finds so far")


# === Step 1: Manual cookie grab (visible browser) ===
options = Options()
# Visible required for manual challenges
options.add_argument("--window-size=1920,1080")
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

selenium_stealth.stealth(driver,
    languages=["en-US", "en"],
    vendor="Google Inc.",
    platform="MacIntel",
    webgl_vendor="Intel Inc.",
    renderer="Intel Iris OpenGL Engine",
    fix_hairline=True,
)

driver.get("https://www.justice.gov/epstein")
print("\n=== MANUAL VERIFICATION STEP ===")
print("Browser opened. Solve any anti-bot, age gate, Queue-IT, or captcha.")
print("Test by opening a direct file URL (e.g., paste a .pdf link) – it should load without redirect.")
print("When access is clear, press Enter here to export cookies and start probing.")
input("Press Enter to continue...")

# Export cookies
cookies = driver.get_cookies()
with open(COOKIES_FILE, 'w') as f:
    json.dump(cookies, f)
print(f"Cookies exported – closing browser")
driver.quit()

# === Step 2: Parallel probing with requests ===
session = requests.Session()
with open(COOKIES_FILE) as f:
    cookies = json.load(f)
for cookie in cookies:
    session.cookies.set(cookie['name'], cookie['value'])

session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    'Referer': 'https://www.justice.gov/epstein'
})

def refresh_cookies_and_session():
    global session, consecutive_401s
    print("\\n🔄 *** BOT BLOCKED (401 burst) - HUMAN INTERVENTION ***")
    print("1. Change VPN/IP.")
    print("2. Browser reopens - solve challenges, test .mp4.")
    print("3. Enter to resume.")
    
    options = Options()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    selenium_stealth.stealth(driver,
        languages=["en-US", "en"],
        vendor="Google Inc.",
        platform="MacIntel",
        webgl_vendor="Intel Inc.",
        renderer="Intel Iris OpenGL Engine",
        fix_hairline=True)
    driver.get("https://www.justice.gov/epstein")
    input("Done? Enter...")
    cookies = driver.get_cookies()
    driver.quit()
    
    session.cookies.clear()
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'])
    print("✅ Cookies refreshed - resuming...")
    consecutive_401s = 0


def probe_url(stem, ext):
    test_url = stem + ext
    try:
        r = session.head(test_url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        status = r.status_code
        global consecutive_401s, error_threshold
        if status == 401:
            consecutive_401s += 1
            print(f"401 burst #{consecutive_401s}/{error_threshold} {ext} {stem[-40:]}")
            if consecutive_401s >= error_threshold:
                refresh_cookies_and_session()
                return None
        elif status != 200:
            consecutive_401s = 0
            ct = r.headers.get('Content-Type', '').lower()
            final_url = r.url
            print(f"NON200 {ext} {stem[-40:]} → status={status} final={final_url[-60:]} CT={ct}")
        if status == 200:
            ct = r.headers.get('Content-Type', '').lower()
            size = int(r.headers.get('Content-Length', 0))
            if size < 1024 * 100:  # Skip <100KB fakes
                print(f"TINY {size/1024:.1f}KB {ext} {stem[-40:]} skip")
                # Still record the find, but with a special 'tiny_file' type
                return {'actual_url': test_url, 'media_type': 'tiny_file', 'size_bytes': size}
            
            if any(m in ct for m in ['video/', 'image/', 'audio/']):
                print(f"VALID {size/1024/1024:.1f}MB {ct[:20]} {ext} OK")
                return {'actual_url': test_url, 'media_type': ct, 'size_bytes': size}
        return None
    except Exception as e:
        print(f"ERR {ext} {stem[-40:]} → {str(e)[:80]}")
        return None
    finally:
        time.sleep(random.uniform(0.5, 2.0))

new_finds = 0
for ext in MEDIA_EXTENSIONS:
    total_pdf = sum(1 for u in urls if u.lower().endswith('.pdf'))
    media_stems = len(updates)
    stems_to_probe = [u.rsplit('.', 1)[0] for u in urls if u.lower().endswith('.pdf') and u.rsplit('.', 1)[0] not in processed_stems and u.rsplit('.', 1)[0] not in updates]
    print(f"DEBUG {ext}: Total PDF={total_pdf} Media={media_stems} Probe={len(stems_to_probe)}")
    if len(stems_to_probe) == 0:
        print(f"  Skipping {ext} - all probed!")
    print(f"DEBUG: Probing {len(stems_to_probe)} stems for {ext}")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_stem = {executor.submit(probe_url, stem, ext): stem for stem in stems_to_probe}
        for i, future in enumerate(as_completed(future_to_stem), 1):
            stem = future_to_stem[future]
            result = future.result()
            if result:
                updates[stem] = result
                processed_stems.add(stem)
                new_finds += 1
                print(f"FOUND: {stem}{ext} → {result['media_type']}")
            if i % BATCH_SIZE == 0:
                print(f"   Processed {i}/{len(stems_to_probe)} for {ext}")
            if i % 250 == 0:
                save_progress()

    print(f"Found {new_finds} new with {ext} (total finds: {len(updates)})")
    save_progress()

# Final mislabeled .pdf check (parallel)
print("\nChecking original .pdf for mislabeled media...")
total_pdf = sum(1 for u in urls if u.lower().endswith('.pdf'))
media_stems = len(updates)
stems_to_check = [u.rsplit('.', 1)[0] for u in urls if u.lower().endswith('.pdf') and u.rsplit('.', 1)[0] not in updates]
print(f"DEBUG PDF check: Total PDF={total_pdf} Media={media_stems} Probe={len(stems_to_check)}")
if len(stems_to_check) == 0:
    print("  Skipping PDF check - all probed!")
    print(f"DEBUG: PDF check {len(stems_to_check)} stems")

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    future_to_stem = {executor.submit(probe_url, stem, '.pdf'): stem for stem in stems_to_check}
    pdf_batch = 0
    for future in as_completed(future_to_stem):
        stem = future_to_stem[future]
        result = future.result()
        if result:
            updates[stem] = result
            new_finds += 1
            print(f"MISLabeled: {stem}.pdf → {result['media_type']}")
        pdf_batch += 1
        if pdf_batch % 250 == 0:
            save_progress()

    save_progress()

print(f"\nCOMPLETE! {len(updates)} media files found (out of {len(urls)} URLs)")
print(f"Results saved to {OUTPUT_CSV}")

# Optional cleanup
# os.remove(COOKIES_FILE)

# This ensures the script doesn't autorun if imported elsewhere
if __name__ != "__main__":
    print("This script is designed to be run directly, not imported.")
    exit()
