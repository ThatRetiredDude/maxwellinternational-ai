# MaxwellInternational.ai — Roll Your Own

Data collection scripts for use with the Epstein Library. 
**2026 Jared Maxwell · @ThatRetiredDude on 𝕏 · MaxwellInternational.ai**

Licensed under the [Apache License, Version 2.0](LICENSE).

---

## Run order

Run the programs **in this order** (each step uses the previous step’s output):

1. **GetURLs** → collects PDF URLs into a CSV  
2. **xTensionProbe** → checks which URLs are media (video/audio/image) and writes a media CSV  
3. **GetMetaData** → validates and enriches media URLs with metadata (e.g. ffprobe), writes final CSV  

---

## Prerequisites

- **Python 3** with pip  
- **Google Chrome** (for Selenium)  
- **FFmpeg** (for GetMetaData only; on macOS: `brew install ffmpeg`)  
- Install Python dependencies, e.g.:

  ```bash
  pip install selenium webdriver-manager selenium-stealth requests questionary
  ```

  (Or use a `requirements.txt` if you add one.)

---

## 1. GetURLs (`RollYourOwn/GetURLs.py`)

**Purpose:** Opens the DOJ Epstein search page in Chrome, lets you pass any anti-bot/captcha/Queue-IT, then scrapes PDF URLs from the search results (with pagination and resume).

**Input:** None (starts from the live site).

**Output:** `epstein_no_images_pdf_urls.csv` (one column: `URL`).

**On-screen instructions:**

1. A browser opens to the search page. Solve any anti-bot, age gate, captcha, or Queue-IT challenge.
2. In the search box, enter **“no images produced”** and submit.
3. Wait for results to load (PDF links visible).
4. Return to the terminal and press **Enter** to start scraping.
5. The script will paginate and save progress; you can stop and re-run to resume.

**Run:**

```bash
cd RollYourOwn
python GetURLs.py
```

---

## 2. xTensionProbe (`RollYourOwn/xTensionProbe.py`)

**Purpose:** Takes the URL list from GetURLs and checks which links are actually media (video/audio/image) by extension and probing. Uses a short browser session so you can solve challenges once; then it uses cookies for parallel requests. Add to the list of extensions - this goes way back so nothing is off the table. 

**Input:** `epstein_no_images_pdf_urls.csv` (from GetURLs).

**Output:** `epstein_media_checked_urls.csv` (columns include `original_url`, `actual_url`, `media_type`, `size_bytes`, `is_tiny`).

**On-screen instructions:**

1. When the script runs, select which **file extensions** to scan (e.g. `.mp4`, `.mov`, `.jpg`); confirm with Enter.
2. Optionally set the number of **concurrent workers** (default 5).
3. A browser opens. Solve any anti-bot, age gate, Queue-IT, or captcha. Optionally open a direct file URL to confirm access.
4. When access is clear, press **Enter** in the terminal to export cookies and start probing.
5. The script will probe URLs and save progress; you can stop and re-run to resume. If you get blocked (e.g. 401 burst), follow the prompt to change VPN and re-do the browser step.

**Run:**

```bash
cd RollYourOwn
python xTensionProbe.py
```

---

## 3. GetMetaData (`RollYourOwn/GetMetaData.py`)

**Purpose:** Reads the media CSV from xTensionProbe and validates/enriches each media URL using FFmpeg (ffprobe). Supports multiple scan modes (fast partial download, deep scan, etc.) and can rescan invalid files.

**Input:** `epstein_media_checked_urls.csv` (from xTensionProbe).

**Output:** `epstein_full_metadata.csv` (URLs with metadata and validation result).

**On-screen instructions:**

1. Ensure **FFmpeg** is installed (`ffmpeg -version`). On macOS: `brew install ffmpeg`.
2. When the script runs, set **worker count** and whether to use **random sleep** between requests.
3. First run: a browser opens for you to solve challenges and save cookies (same idea as xTensionProbe). Press Enter when done to start.
4. Choose a **scan mode** (e.g. Fast 5MB, Smart auto-escalate, Deep 100MB, or Custom MB).
5. The script downloads a portion of each file, runs ffprobe, and writes results. You can rescan invalid files with a different mode when prompted.

**Run:**

```bash
cd RollYourOwn
python GetMetaData.py
```

---

## Summary

| Step | Script        | Input CSV                     | Output CSV                    |
|------|---------------|-------------------------------|-------------------------------|
| 1    | GetURLs       | —                             | `epstein_no_images_pdf_urls.csv` |
| 2    | xTensionProbe | `epstein_no_images_pdf_urls.csv` | `epstein_media_checked_urls.csv` |
| 3    | GetMetaData   | `epstein_media_checked_urls.csv` | `epstein_full_metadata.csv`   |

Follow the on-screen instructions for each script. Any questions? Contact **@ThatRetiredDude** on 𝕏 or **MaxwellInternational.ai**.
