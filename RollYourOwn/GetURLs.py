import csv
import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
import selenium_stealth
import json
import subprocess
import sys

# Config
CSV_FILE = 'epstein_no_images_pdf_urls.csv'

# Load existing URLs for deduplication/resume
all_urls = set()
if os.path.exists(CSV_FILE):
    try:
        with open(CSV_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            for row in reader:
                if row and row[0].strip():
                    all_urls.add(row[0].strip())
        print(f"Loaded {len(all_urls)} existing URLs – duplicates will be skipped")
    except Exception as e:
        print(f"CSV load error: {e}")

# Browser setup – visible (required for manual steps)
options = Options()

# DEBUG LOGGING
def log_debug(msg, data=None, hypothesis='GENERAL'):
    log_data = {
        "id": f"log_{int(time.time()*1000)}_{hash(msg) % 10000}",
        "timestamp": int(time.time()*1000),
        "location": "ep.py:driver_init",
        "message": msg,
        "data": data or {},
        "runId": "debug_driver_crash",
        "hypothesisId": hypothesis
    }
    log_path = '/Users/mainuser/Desktop/ep/.cursor/debug.log'
    try:
        with open(log_path, 'a') as f:
            f.write(json.dumps(log_data) + '\n')
    except:
        pass  # Silent fail

# Log Chrome version
try:
    chrome_ver = subprocess.check_output(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--version'], stderr=subprocess.STDOUT).decode().strip()
    log_debug("Chrome version", {"chrome_ver": chrome_ver}, "A")
except:
    log_debug("Chrome version check failed", {}, "A")

log_debug("Options list", {"options_args": [arg for arg in options.arguments]}, "C")
# options.add_argument("--headless=new")  # Do NOT enable – need visible for manual
options.add_argument("--window-size=1920,1080")
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)

# Stable Turbo: Block images
prefs = {"profile.managed_default_content_settings.images": 2}
options.add_experimental_option("prefs", prefs)

options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36")

driver_path = ChromeDriverManager().install()
log_debug("ChromeDriver path/version", {"driver_path": driver_path}, "A")

# Test driver binary standalone
try:
    result = subprocess.run([driver_path, '--version'], capture_output=True, timeout=10, text=True)
    log_debug("Standalone driver --version", {"stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "returncode": result.returncode}, "F")
except Exception as e:
    log_debug("Standalone driver test FAILED", {"error": str(e)}, "F")

# Check file perms/quarantine
try:
    stat = os.stat(driver_path)
    log_debug("Driver file stats", {"size": stat.st_size, "mode": oct(stat.st_mode)}, "F")
    xattr_result = subprocess.run(['xattr', '-l', driver_path], capture_output=True, text=True)
    xattr = xattr_result.stdout.strip() or "no xattr"
    log_debug("Driver xattr/quarantine", {"xattr": xattr}, "G")
except Exception as e:
    log_debug("File check FAILED", {"error": str(e)}, "G")

service = Service(driver_path)
try:
    driver = webdriver.Chrome(service=service, options=options)
    log_debug("Driver started successfully", {}, "GENERAL")
except Exception as e:
    log_debug("Driver init FAILED", {"error": str(e)}, "GENERAL")
    raise

selenium_stealth.stealth(driver,
    languages=["en-US", "en"],
    vendor="Google Inc.",
    platform="MacIntel",
    webgl_vendor="Intel Inc.",
    renderer="Intel Iris OpenGL Engine",
    fix_hairline=True,
)

# On-screen attribution (assistance message)
_CONTACT = "\033[40;97m @ThatRetiredDude on 𝕏 or MaxwellInternational.ai \033[0m"
print("Follow the on-screen instructions. Any questions? Contact", _CONTACT)

# Open the search page
driver.get("https://www.justice.gov/epstein/search")
print("Browser opened to search page.")

# MANUAL PAUSE
print("\n=== MANUAL STEP ===")
print("1. Solve any anti-bot, age gate, captcha, or Queue-IT challenge.")
print("2. Enter 'no images produced' in the search box and submit.")
print("3. Wait for results to load (PDF links visible).")
print("4. When ready, come back here and press Enter to start scraping.")
input("Press Enter to begin scraping...")

# Helper function to wait for PDF links using robust detection
def wait_for_pdf_links(timeout=15):
    """Wait for PDF links to be present using robust detection (handles query params)"""
    for _ in range(timeout):
        all_links = driver.find_elements(By.TAG_NAME, 'a')
        pdf_count = sum(1 for link in all_links 
                       if link.get_attribute('href') and '.pdf' in link.get_attribute('href').lower())
        if pdf_count > 0:
            return True
        time.sleep(0.5)
    return False

# Helper function to check if PDF links exist
def has_pdf_links():
    """Check if PDF links exist using robust detection"""
    all_links = driver.find_elements(By.TAG_NAME, 'a')
    return any(link.get_attribute('href') and '.pdf' in link.get_attribute('href').lower() 
               for link in all_links)

# Confirm results are loaded - FIXED: Use robust detection instead of restrictive selector
try:
    WebDriverWait(driver, 30).until(lambda d: has_pdf_links())
    print("PDF results detected – starting scrape")
except:
    print("No PDF links found – check browser manually")
    driver.save_screenshot("manual_error.png")
    driver.quit()
    raise SystemExit

# Save function (atomic, fast)
def save_progress():
    temp_file = CSV_FILE + '.tmp'
    with open(temp_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["URL"])
        for url in all_urls:
            writer.writerow([url])
    os.replace(temp_file, CSV_FILE)
    # print(f"   → Saved progress: {len(all_urls)} URLs")

# Current page detection
def get_current_page():
    try:
        current = driver.find_element(By.CSS_SELECTOR, 'a[aria-current="page"]')
        aria = current.get_attribute("aria-label")
        return int(aria.split()[-1])
    except:
        return 1

save_counter = 0
page_counter = get_current_page()
while True:
    before = len(all_urls)
    
    # FIXED: Wait for PDF links specifically, not just any links
    if not wait_for_pdf_links(timeout=10):
        print(f"Warning: No PDF links found on page {page_counter} after waiting")
    
    # FIXED: Scroll to trigger lazy loading of PDF links
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)  # Brief pause for lazy loading
    
    # Scroll down gradually to trigger any lazy-loaded content
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
    time.sleep(0.5)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(0.5)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)
        
    # Robust PDF finder (case-insensitive, query params OK)
    all_links = driver.find_elements(By.TAG_NAME, 'a')
    pdf_links = []
    for link in all_links:
        href = link.get_attribute('href')
        if href and '.pdf' in href.lower():
            pdf_links.append(link)
    new_added = 0
    for link in pdf_links:
        url = link.get_attribute('href')
        if url and url not in all_urls:
            all_urls.add(url)
            new_added += 1
    
    print(f"Page {page_counter}: {len(pdf_links)} links → {new_added} new → Total: {len(all_urls)}")
    if new_added > 0:
        save_counter += 1
        if save_counter % 5 == 0:  # Save every 5 pages for speed
            save_progress()
            save_counter = 0
    
    # Click Next (multiple selectors)
    next_clicked = False
    next_selectors = [
        '//a[@rel="next"]',
        '//a[contains(@aria-label, "Next")]',
        '//a[text()="Next" or text()=">"]',
        '//button[contains(text(), "Next")]'
    ]
    for sel in next_selectors:
        try:
            # TURBO: Reduced wait time
            next_btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, sel)))
            driver.execute_script("arguments[0].scrollIntoView(true);", next_btn)
            driver.execute_script("arguments[0].click();", next_btn)
            next_clicked = True
            break
        except:
            continue
    
    if not next_clicked:
        print("No Next button – end of results reached")
        break
    
    # print("Clicked Next")
    
    # Wait for page number change (ensures real navigation)
    old_page = page_counter
    try:
        WebDriverWait(driver, 10).until(
            lambda d: get_current_page() > old_page
        )
        page_counter = get_current_page()
        # print(f"Advanced to page {page_counter}")
    except:
        print("Page didn't advance – possible block. Screenshot saved.")
        driver.save_screenshot(f"stuck_{old_page}.png")
        break
    
    # FIXED: Wait for PDF links to load after navigation
    if not wait_for_pdf_links(timeout=10):
        print(f"Warning: No PDF links found on page {page_counter} after navigation")

# Final sorted save
print("Finalizing sorted CSV...")
sorted_urls = sorted(all_urls)
with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(["URL"])
    for url in sorted_urls:
        writer.writerow([url])

print(f"DONE! {len(all_urls)} unique URLs saved (~{len(all_urls)//10} pages)")
driver.quit()