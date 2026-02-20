import csv
import subprocess
import json
import os
import random
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import selenium_stealth

import questionary

# --- Configuration ---
INPUT_CSV = 'epstein_media_checked_urls.csv'
OUTPUT_CSV = 'epstein_full_metadata.csv'
COOKIES_FILE = 'doj_cookies_metadata.json' # Use a separate cookie file
MAX_WORKERS = 15  # Default worker count
PROBE_SIZE_MB = 5 # How many MB to download to check metadata
DEEP_SCAN_SIZE_MB = 100 # How many MB to download for deep scan
SUPERDEEP_SCAN_SIZE_MB = 200 # Superdeep scan size
MANDINGO_DEEP_SCAN_SIZE_MB = 500 # Mandingo deep scan size
SAVE_BATCH_SIZE = 50 # Save progress every N files
RANDOM_SLEEP = False

# --- 401 Handling Globals ---
consecutive_401s = 0
ERROR_THRESHOLD = 5
refresh_lock = threading.Lock()

# --- Helper to check for FFmpeg ---
def is_ffmpeg_installed():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ FFmpeg is not installed or not in your PATH.")
        print("Please install it to continue. On macOS: 'brew install ffmpeg'")
        return False

def get_cookies():
    """Opens a browser for the user to solve challenges and saves cookies."""
    print("🚀 Starting browser for manual verification...")
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
        languages=["en-US", "en"], vendor="Google Inc.", platform="MacIntel",
        webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)
    
    driver.get("https://www.justice.gov/epstein")
    print("\n=== MANUAL VERIFICATION STEP ===")
    print("1. Solve any anti-bot, captcha, or Queue-IT challenges.")
    print("2. Test by opening a direct media URL to ensure it loads.")
    print("3. When access is clear, press Enter here to save cookies and start.")
    input("Press Enter to continue...")
    
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, 'w') as f:
        json.dump(cookies, f)
    print(f"✅ Cookies saved to {COOKIES_FILE}. Closing browser.")
    driver.quit()
    return cookies

def refresh_cookies_and_session(session):
    """Pauses execution to allow user to change VPN and refresh cookies."""
    global consecutive_401s
    with refresh_lock:
        # Double-check inside the lock
        if consecutive_401s < ERROR_THRESHOLD:
            return

        print(f"\n🔄 *** BOT BLOCKED ({ERROR_THRESHOLD} consecutive auth errors) - HUMAN INTERVENTION REQUIRED ***")
        print("1. Please CHANGE YOUR VPN IP now.")
        print("2. Once changed, press Enter to open the browser and solve challenges.")
        input("Press Enter to continue...")

        # Get new cookies
        new_cookies = get_cookies()
        
        # Update the session in-place
        session.cookies.clear()
        for cookie in new_cookies:
            session.cookies.set(cookie['name'], cookie['value'])
        
        print("✅ Cookies refreshed and session updated. Resuming...")
        consecutive_401s = 0

def flatten_dict(d, parent_key='', sep='_'):
    """Flattens a nested dictionary."""
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

def run_ffprobe(session, url, size_mb=None):
    """
    Core ffprobe logic. Downloads specified MB chunk and runs ffprobe.
    Includes retry logic for 401/403 errors.
    
    Args:
        session: Authenticated requests session
        url: URL to probe
        size_mb: Size in MB to download (None uses PROBE_SIZE_MB for backward compat)
    """
    global consecutive_401s
    
    if size_mb is None:
        size_mb = PROBE_SIZE_MB
    
    while True:
        if RANDOM_SLEEP:
            time.sleep(random.uniform(0.5, 1.5))

        try:
            # Download specified chunk size via authenticated session
            headers = {'Range': f'bytes=0-{size_mb * 1024 * 1024}'}
            
            response = session.get(url, headers=headers, stream=True, timeout=60)
            
            if response.status_code in [401, 403]:
                consecutive_401s += 1
                if consecutive_401s >= ERROR_THRESHOLD:
                    refresh_cookies_and_session(session)
                else:
                    # Small delay before retry
                    time.sleep(random.uniform(1, 3))
                continue # Retry this request

            response.raise_for_status()
            consecutive_401s = 0 # Reset on success
            
            content_to_probe = response.content
            if not content_to_probe:
                return {'is_valid': False, 'error': 'empty_response_body'}

            command = [
                'ffprobe', '-v', 'quiet', '-print_format', 'json',
                '-show_streams', '-show_format', '-'
            ]
            result = subprocess.run(command, input=content_to_probe, capture_output=True, timeout=60)
            
            if result.returncode != 0:
                return {'is_valid': False, 'error': f'ffprobe_error: {result.stderr.decode()[:200]}'}

            data = json.loads(result.stdout)
            
            if not data.get('streams'):
                return {'is_valid': False, 'error': 'no_media_streams'}

            metadata = {'is_valid': True, 'validation_method': f'{size_mb}MB_scan'}
            
            if 'format' in data:
                metadata.update(flatten_dict(data['format'], parent_key='format'))
                # Ensure a top-level size column for easy access
                metadata['file_size_bytes'] = data['format'].get('size')

            for i, stream in enumerate(data.get('streams', [])):
                codec_type = stream.get('codec_type', 'unknown')
                metadata.update(flatten_dict(stream, parent_key=f'stream_{i}_{codec_type}'))
                
            return metadata

        except requests.exceptions.RequestException as e:
            return {'is_valid': False, 'error': f'http_error: {e}'}
        except Exception as e:
            return {'is_valid': False, 'error': f'general_error: {e}', 'url': url}

def validate_url_entry(url, session, scan_mode, custom_size_mb=None):
    """
    Orchestrates the validation based on the user's chosen scan mode.
    Supports auto-escalation for two-pass mode through all scan levels.
    
    Args:
        url: URL to validate
        session: Authenticated requests session
        scan_mode: Scan mode ('fast', 'full', 'superdeep', 'mandingo', 'two-pass', 'custom')
        custom_size_mb: Custom MB size (required if scan_mode is 'custom')
    """
    if 'no_media_yet' in url or 'pdf_or_not_found' in url:
        return {'is_valid': False, 'error': 'skipped_unsolved'}

    if scan_mode == 'fast':
        return run_ffprobe(session, url, size_mb=PROBE_SIZE_MB)
    
    elif scan_mode == 'full':
        return run_ffprobe(session, url, size_mb=DEEP_SCAN_SIZE_MB)
    
    elif scan_mode == 'superdeep':
        return run_ffprobe(session, url, size_mb=SUPERDEEP_SCAN_SIZE_MB)
    
    elif scan_mode == 'mandingo':
        return run_ffprobe(session, url, size_mb=MANDINGO_DEEP_SCAN_SIZE_MB)
    
    elif scan_mode == 'custom':
        if custom_size_mb is None:
            return {'is_valid': False, 'error': 'custom_size_mb_required'}
        return run_ffprobe(session, url, size_mb=custom_size_mb)

    elif scan_mode == 'two-pass':
        # Auto-escalate through all scan levels: 5MB → 100MB → 200MB → 500MB
        scan_levels = [
            (PROBE_SIZE_MB, "Fast"),
            (DEEP_SCAN_SIZE_MB, "Deep"),
            (SUPERDEEP_SCAN_SIZE_MB, "Superdeep"),
            (MANDINGO_DEEP_SCAN_SIZE_MB, "Mandingo Deep")
        ]
        
        last_error = None
        for idx, (size_mb, level_name) in enumerate(scan_levels):
            result = run_ffprobe(session, url, size_mb=size_mb)
            
            if result.get('is_valid'):
                if size_mb > PROBE_SIZE_MB:
                    print(f"  ✅ {level_name} scan ({size_mb}MB) succeeded for {os.path.basename(url)}")
                return result
            
            # Store error for potential return
            last_error = result.get('error', 'unknown_error')
            
            # If we got streams but validation failed for other reasons, don't escalate
            if "no_media_streams" not in last_error:
                return result
            
            # Continue to next level if no streams found and more levels available
            if idx < len(scan_levels) - 1:
                next_size, next_name = scan_levels[idx + 1]
                print(f"  ⬆️  Escalating to {next_name} scan ({next_size}MB) for {os.path.basename(url)}...")
        
        # All levels failed
        return {'is_valid': False, 'error': f'all_scan_levels_failed: {last_error}'}
    
    return {'is_valid': False, 'error': 'invalid_scan_mode'}

def save_results_to_csv(results, file_path):
    if not results:
        return
        
    # Dynamically generate all possible headers from the collected data
    all_headers = set()
    for res in results:
        all_headers.update(res.keys())
    
    # Define a preferred order for key columns
    preferred_order = ['original_url', 'actual_url', 'media_type', 'is_valid', 'validation_method', 'file_size_bytes', 'error']
    sorted_headers = sorted(list(all_headers), key=lambda h: (preferred_order.index(h) if h in preferred_order else len(preferred_order), h))

    with open(file_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=sorted_headers, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    print(f"💾 Progress saved for {len(results)} URLs to {file_path}")

def main():
    global MAX_WORKERS, RANDOM_SLEEP
    if not is_ffmpeg_installed():
        exit(1)

    _CONTACT = "\033[40;97m @ThatRetiredDude on 𝕏 or MaxwellInternational.ai \033[0m"
    print("Follow the on-screen instructions. Any questions? Contact", _CONTACT)

    print("\n--- Metadata Validator Configuration ---")
    
    # --- Worker Count Selection ---
    MAX_WORKERS_str = questionary.text(
        f"Enter number of concurrent workers [1-50, default: {MAX_WORKERS}]:",
        default=str(MAX_WORKERS)
    ).ask()
    try:
        user_max_workers = int(MAX_WORKERS_str)
        if 1 <= user_max_workers <= 100:
            MAX_WORKERS = user_max_workers
    except: pass

    RANDOM_SLEEP = questionary.confirm(
        "Enable random sleep between requests? (Helps avoid detection)",
        default=True
    ).ask()

    # --- Load existing results to support resume ---
    processed_urls = {}
    if os.path.exists(OUTPUT_CSV):
        print(f"📂 Loading existing results from: {OUTPUT_CSV}")
        with open(OUTPUT_CSV, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'is_valid' in row:
                    row['is_valid'] = (row['is_valid'] == 'True')
                processed_urls[row['actual_url']] = row
        valid_count = sum(1 for r in processed_urls.values() if r.get('is_valid'))
        invalid_count = len(processed_urls) - valid_count
        print(f"   Found {len(processed_urls)} URLs ({valid_count} valid, {invalid_count} invalid)")

    # --- Get authenticated cookies once at startup ---
    if not os.path.exists(COOKIES_FILE):
        print("\n🔐 No saved cookies found. Opening browser for authentication...")
        cookies = get_cookies()
    else:
        print(f"\n🔐 Loading saved cookies from {COOKIES_FILE}")
        with open(COOKIES_FILE, 'r') as f:
            cookies = json.load(f)
        refresh = questionary.confirm(
            "Refresh cookies? (Open browser to solve new challenges)",
            default=False
        ).ask()
        if refresh:
            cookies = get_cookies()
    
    session = requests.Session()
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'])
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })

    # --- Read source URLs ---
    if not os.path.exists(INPUT_CSV):
        print(f"❌ Error: {INPUT_CSV} not found. Please run probe.py first.")
        exit(1)
    
    source_rows = []
    with open(INPUT_CSV, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row['media_type'] in ['no_media_yet', 'pdf_or_not_found']:
                continue
            source_rows.append(row)

    # --- Iterative workflow loop ---
    iteration = 1
    while True:
        print(f"\n{'='*60}")
        print(f"📊 ITERATION {iteration}")
        print(f"{'='*60}")
        
        # Build queue of URLs to validate
        rows_to_validate = []
        for row in source_rows:
            url = row['actual_url']
            if url not in processed_urls:
                rows_to_validate.append(row)
        
        if not rows_to_validate:
            invalid_count = sum(1 for r in processed_urls.values() if not r.get('is_valid'))
            if invalid_count == 0:
                print("\n✅ All URLs have been validated successfully!")
                break
            
            print(f"\n⚠️  No new URLs to scan. Found {invalid_count} invalid files.")
            rescan = questionary.confirm(
                f"Rescan {invalid_count} invalid files?",
                default=False
            ).ask()
            
            if not rescan:
                break
            
            # Add invalid URLs to queue
            for row in source_rows:
                url = row['actual_url']
                if url in processed_urls and not processed_urls[url].get('is_valid'):
                    rows_to_validate.append(row)
        
        if not rows_to_validate:
            break
        
        # Choose scan mode
        scan_mode = questionary.select(
            f"Choose scan mode for {len(rows_to_validate)} URLs:",
            choices=[
                questionary.Choice("🚀 Fast (5MB partial scan)", "fast"),
                questionary.Choice("🔄 Smart (Auto-escalate: 5MB → 100MB → 200MB → 300MB)", "two-pass"),
                questionary.Choice("🔥 Deep (100MB scan on all)", "full"),
                questionary.Choice("🔥🔥 Superdeep (200MB scan on all)", "superdeep"),
                questionary.Choice("🔥🔥🔥 Mandingo Deep (500MB scan on all)", "mandingo"),
                questionary.Choice("🙈 Custom (Enter your own MB size)", "custom"),
            ],
            default="fast" if iteration == 1 else "full"
        ).ask()
        if not scan_mode:
            break

        # Prompt for custom size if custom mode selected
        custom_size_mb = None
        if scan_mode == 'custom':
            custom_size_str = questionary.text(
                "Enter custom scan size in MB:",
                default="500"
            ).ask()
            try:
                custom_size_mb = int(custom_size_str)
                if custom_size_mb <= 0:
                    print("❌ Invalid size. Must be greater than 0. Using default 500MB.")
                    custom_size_mb = 500
            except (ValueError, TypeError):
                print("❌ Invalid input. Using default 500MB.")
                custom_size_mb = 500
            print(f"\n🔍 Using custom scan size: {custom_size_mb}MB")

        scan_mode_display = f"{scan_mode} ({custom_size_mb}MB)" if scan_mode == 'custom' else scan_mode
        print(f"\n🔍 Running '{scan_mode_display}' scan on {len(rows_to_validate)} URLs...")

        # Process in parallel
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_row = {executor.submit(validate_url_entry, row['actual_url'], session, scan_mode, custom_size_mb): row 
                            for row in rows_to_validate}
            
            for i, future in enumerate(as_completed(future_to_row)):
                original_row = future_to_row[future]
                url = original_row['actual_url']
                metadata = future.result()
                
                full_row_data = {**original_row, **metadata}
                processed_urls[url] = full_row_data
                
                is_valid_str = "✅" if metadata.get('is_valid') else "❌"
                print(f"({i+1}/{len(rows_to_validate)}) {is_valid_str} {os.path.basename(url)}")

                if (i + 1) % SAVE_BATCH_SIZE == 0:
                    save_results_to_csv(list(processed_urls.values()), OUTPUT_CSV)

        # Final save
        save_results_to_csv(list(processed_urls.values()), OUTPUT_CSV)
        
        # Show results
        valid_count = sum(1 for r in processed_urls.values() if r.get('is_valid'))
        invalid_count = len(processed_urls) - valid_count
        print(f"\n📈 Results: {valid_count} valid, {invalid_count} invalid out of {len(processed_urls)} total")
        
        # Ask if want to continue
        if invalid_count > 0:
            continue_scan = questionary.confirm(
                f"Rescan {invalid_count} invalid files with a different mode?",
                default=False
            ).ask()
            if not continue_scan:
                break
        else:
            print("\n✅ All files validated successfully!")
            break
        
        iteration += 1

    valid_count = sum(1 for r in processed_urls.values() if r.get('is_valid'))
    invalid_count = len(processed_urls) - valid_count
    print(f"\n🎉 Final results: {valid_count} valid, {invalid_count} invalid out of {len(processed_urls)} total")

if __name__ == "__main__":
    main()
