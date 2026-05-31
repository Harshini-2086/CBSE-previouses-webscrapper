# CBSE Class 10 Paper Downloader — Complete Guide

> **Downloads** question papers + marking schemes for **Mathematics, Science,
> English, Social Studies, Information Technology, and Hindi** (2010 – 2025)
> from `cbseacademic.nic.in`.

---

## Table of Contents
1. [Project Directory Layout](#1-project-directory-layout)
2. [Prerequisites](#2-prerequisites)
3. [Installation (step-by-step)](#3-installation-step-by-step)
4. [The Python Script — Annotated Overview](#4-the-python-script--annotated-overview)
5. [How to Run](#5-how-to-run)
6. [Output Folder Structure](#6-output-folder-structure)
7. [Troubleshooting](#7-troubleshooting)
8. [Ethical & Legal Note](#8-ethical--legal-note)

---

## 1. Project Directory Layout

Before you run anything, your project folder should look like this:

```
cbse_downloader/          ← you create this folder
├── cbse_scraper.py       ← the main script (copy from this repo)
├── requirements.txt      ← dependency list (copy from this repo)
└── (venv/)               ← virtual environment (created in Step 3)
```

After running the script, new folders are created automatically:

```
cbse_downloader/
├── cbse_scraper.py
├── requirements.txt
├── cbse_scraper.log       ← full activity log (auto-created)
├── download_summary.json  ← machine-readable stats (auto-created)
├── venv/
└── CBSE_Papers/           ← all downloaded PDFs live here
    ├── Mathematics/
    │   ├── 2024/
    │   │   ├── Set_1/
    │   │   │   ├── Question_Paper.pdf
    │   │   │   └── Marking_Scheme.pdf
    │   │   ├── Set_2/
    │   │   │   ├── Question_Paper.pdf
    │   │   │   └── Marking_Scheme.pdf
    │   │   └── Compartment/
    │   │       ├── Question_Paper.pdf
    │   │       └── Marking_Scheme.pdf
    │   └── 2023/
    │       └── ...
    ├── Science/
    │   └── ...
    ├── English/
    ├── Social_Studies/
    ├── Information_Technology/
    └── Hindi/
```

---

## 2. Prerequisites

| Requirement | Minimum Version | How to Check |
|---|---|---|
| Python | **3.10+** | `python --version` or `python3 --version` |
| pip | bundled with Python | `pip --version` |
| Internet connection | — | needed at run time |

### Install Python (if missing)

- **Windows**: Download from [python.org/downloads](https://www.python.org/downloads/).
  During installation, ✅ check **"Add Python to PATH"**.
- **macOS**: `brew install python` (requires [Homebrew](https://brew.sh)) or download from python.org.
- **Linux (Debian/Ubuntu)**: `sudo apt update && sudo apt install python3 python3-pip python3-venv`

---

## 3. Installation (step-by-step)

### Step 3a — Create the project folder

```bash
# On Windows (Command Prompt or PowerShell)
mkdir cbse_downloader
cd cbse_downloader

# On macOS / Linux (Terminal)
mkdir cbse_downloader
cd cbse_downloader
```

### Step 3b — Copy the script files

Place `cbse_scraper.py` and `requirements.txt` inside the `cbse_downloader/` folder.

### Step 3c — Create a virtual environment

A virtual environment keeps these packages isolated from your system Python.

```bash
# All platforms — run inside cbse_downloader/
python -m venv venv
```

> If `python` doesn't work, try `python3 -m venv venv`.

### Step 3d — Activate the virtual environment

**Windows (Command Prompt)**
```cmd
venv\Scripts\activate.bat
```

**Windows (PowerShell)**
```powershell
venv\Scripts\Activate.ps1
```
> If you see a security error in PowerShell, run:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
> then try activating again.

**macOS / Linux**
```bash
source venv/bin/activate
```

✅ **You'll know it worked** when your terminal prompt changes to show `(venv)`:
```
(venv) C:\cbse_downloader>   ← Windows example
(venv) user@mac cbse_downloader %   ← macOS example
```

### Step 3e — Install dependencies

```bash
pip install -r requirements.txt
```

This installs:

| Package | Purpose |
|---|---|
| `requests` | Makes HTTP GET requests to download pages and PDFs |
| `beautifulsoup4` | Parses HTML to find PDF links |
| `lxml` | Fast HTML/XML parser used by BeautifulSoup |
| `tqdm` | Draws progress bars in the terminal |

> **No Selenium or Playwright needed** — the CBSE academic portal serves its
> links as plain HTML without JavaScript rendering requirements.

---

## 4. The Python Script — Annotated Overview

Here is a section-by-section explanation of what `cbse_scraper.py` does.

### 4a. Configuration block (top of file)
```python
START_YEAR = 2010
END_YEAR   = 2025
REQUEST_DELAY = 1.5   # seconds between requests — be polite to the server
MAX_RETRIES   = 3     # retry failed downloads up to 3 times
```
You can edit these numbers directly if you need fewer years or more retries.

### 4b. SUBJECTS dictionary
```python
SUBJECTS = {
    "Mathematics": ["mathematics", "maths", "041", ...],
    "Science":     ["science", "086", ...],
    ...
}
```
Maps each friendly subject name to keyword patterns (including CBSE subject
codes like `041` for Maths). These are used to filter which PDF links on a
page belong to each subject.

### 4c. `fetch_page()` — GET a web page
Sends an HTTP request and returns a BeautifulSoup object. Implements:
- Retry loop with exponential back-off
- Graceful handling of 404, 403, timeouts, and connection errors

### 4d. `collect_pdf_links()` — Extract PDF links from a page
Scans every `<a href>` on a page and keeps only those that:
1. Point to a `.pdf` file
2. Contain the target year (in the URL or nearby heading)
3. Contain a subject keyword

### 4e. `discover_links_for_year_subject()` — Multi-URL discovery
Tries **all known CBSE portal URLs and year-specific templates** for a given
year + subject. Also follows one level of sub-links to catch deep-nested
pages. This ensures the script works even when CBSE changes its URL structure.

### 4f. `download_pdf()` — Save a single file
- Streams the response in 8 KB chunks (avoids loading large PDFs into RAM)
- Skips files that already exist (safe to re-run)
- Validates content-type to avoid saving HTML error pages as `.pdf`

### 4g. `run()` — Main loop
Combines all the above: iterates over every `(year, subject)` pair, discovers
links, and downloads each file to the correct nested folder.

---

## 5. How to Run

Make sure your virtual environment is **activated** (you see `(venv)` in the prompt).

### Run everything (all subjects, 2010–2025)
```bash
python cbse_scraper.py
```

### Download only specific years
```bash
python cbse_scraper.py --years 2022 2023 2024
```

### Download only specific subjects
```bash
python cbse_scraper.py --subjects Mathematics Science
```

### Combine filters
```bash
python cbse_scraper.py --years 2020 2021 2022 --subjects Mathematics Science English
```

### Dry run — see what would be downloaded without saving files
```bash
python cbse_scraper.py --dry-run
```
Use this first to verify the script finds links before committing to a full
download run.

### Change output directory
```bash
python cbse_scraper.py --output /path/to/my/folder
```

### Slow down requests (more polite to the server)
```bash
python cbse_scraper.py --delay 3.0
```

### All available options
```bash
python cbse_scraper.py --help
```

---

## 6. Output Folder Structure

```
CBSE_Papers/
├── Mathematics/
│   ├── 2024/
│   │   ├── Set_1/
│   │   │   ├── Question_Paper.pdf
│   │   │   └── Marking_Scheme.pdf
│   │   ├── Set_2/
│   │   │   ├── Question_Paper.pdf
│   │   │   └── Marking_Scheme.pdf
│   │   ├── Set_3/
│   │   │   ├── Question_Paper.pdf
│   │   │   └── Marking_Scheme.pdf
│   │   ├── Compartment/
│   │   │   ├── Question_Paper.pdf
│   │   │   └── Marking_Scheme.pdf
│   │   ├── Delhi/
│   │   │   ├── Question_Paper.pdf
│   │   │   └── Marking_Scheme.pdf
│   │   └── Sample_Paper/
│   │       ├── Question_Paper.pdf
│   │       └── Marking_Scheme.pdf
│   └── 2023/
│       └── ...
├── Science/
├── English/
├── Social_Studies/
├── Information_Technology/
└── Hindi/
```

Paper type folders created depend on what CBSE publishes for each year.
Possible types: `Set_1`, `Set_2`, `Set_3`, `Compartment`, `Delhi`,
`Outside_Delhi`, `All_India`, `Sample_Paper`, `General`.

---

## 7. Troubleshooting

### Problem 1 — `ModuleNotFoundError: No module named 'requests'`

**Cause**: You forgot to activate the virtual environment, or `pip install`
was not run.

**Fix**:
```bash
# Activate venv first (see Step 3d), then:
pip install -r requirements.txt
```

---

### Problem 2 — `ConnectionError` or `requests.exceptions.Timeout`

**Cause**: Your internet connection dropped, or the CBSE server is temporarily
slow / unavailable.

**Fix**:
- Check your internet connection.
- Increase the timeout by editing `TIMEOUT = 30` → `TIMEOUT = 60` in the script.
- Increase delay: `python cbse_scraper.py --delay 3.0`
- Simply re-run the script — already-downloaded files are skipped automatically,
  so you won't re-download anything.

---

### Problem 3 — Script runs but `CBSE_Papers/` folder is empty (0 files found)

**Cause**: CBSE has reorganised its website structure, making the hardcoded
URLs outdated.

**Fix — Step by step**:
1. Open `cbse_scraper.py` in any text editor.
2. Go to **[cbseacademic.nic.in](https://cbseacademic.nic.in)** in your browser
   and manually navigate to a question paper page.
3. Copy that URL and **add it to the `CBSE_ENTRY_POINTS` list** near the top
   of the script:
   ```python
   CBSE_ENTRY_POINTS = [
       "https://cbseacademic.nic.in/...",   # ← add new URL here
       ...
   ]
   ```
4. Save the script and re-run.

---

### Problem 4 — PowerShell says "running scripts is disabled"

**Cause**: Windows execution policy blocks unsigned scripts.

**Fix**:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
Then activate the venv again.

---

### Problem 5 — Downloaded files are 0 KB or not valid PDFs

**Cause**: The server returned an HTML error page (e.g., a login wall or
CAPTCHA) instead of the actual PDF.

**Fix**:
- Open the URL from `cbse_scraper.log` in your browser. If it redirects to a
  login page, those files require authentication and cannot be automatically
  downloaded.
- Check if the file on the CBSE portal is genuinely publicly accessible.

---

## 8. Ethical & Legal Note

- This script downloads **publicly available** materials from an **official
  government education portal** for **personal study use only**.
- It inserts a **polite delay** between requests to avoid overloading the
  server.
- Do **not** redistribute downloaded papers commercially or claim ownership.
- If CBSE requests robots.txt compliance, check `cbseacademic.nic.in/robots.txt`
  before running.
- Use responsibly. 🙏
# CBSE-previouses-webscrapper
