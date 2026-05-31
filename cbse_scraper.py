"""
CBSE Class 10 Paper Downloader  v2.0
=====================================
Downloads question papers + marking schemes for Class 10 core subjects
across three CBSE portals:

  SOURCE A  2022–2026  cbse.gov.in/cbsenew/question-paper.html  &  marking-scheme.html
  SOURCE B  2013–2021  cbse.gov.in/newsite_old/examination.html  (hub → sub-pages)
  SOURCE C  2014–2026  cbseacademic.nic.in/sqp_archive.html      (Sample Question Papers)

Subjects: Mathematics, Science, English, Social Studies,
          Information Technology, Hindi

Usage:
  python cbse_scraper.py                            # all subjects, 2013–2026
  python cbse_scraper.py --years 2022 2023 2024     # specific years
  python cbse_scraper.py --subjects Mathematics     # specific subject
  python cbse_scraper.py --dry-run                  # discover links, no download
  python cbse_scraper.py --delay 2.0                # slower/polite pace
"""

import re
import time
import json
import zipfile
import argparse
import logging
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote, quote

import requests
from bs4 import BeautifulSoup

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION — edit here if you need to tweak behaviour
# ═══════════════════════════════════════════════════════════════════════════════

BASE_OUTPUT_DIR = Path("CBSE_Papers")   # root folder for all downloads
REQUEST_DELAY   = 1.5                   # seconds between HTTP requests (be polite)
TIMEOUT         = 30                    # per-request timeout in seconds
MAX_RETRIES     = 3                     # retry failed requests up to this many times

# ─── Subject filter keywords ───────────────────────────────────────────────────
# Keys   = folder names created on disk
# Values = lowercase substrings matched against the subject text on each page
SUBJECTS = {
    "Mathematics": [
        "mathematics", "maths basic", "maths standard",
        "mathematics basic", "mathematics standard",
        "mathematics (basic)", "mathematics (standard)",
        "041_mathematics", "241_mathematics",
    ],
    "Science": [
        "science",
        "086_science",
    ],
    "English": [
        "english language", "english literature",
        "english (language", "english l&l",
         "184_english",
    ],
    "Social_Studies": [
        "social science", "social studies",
        "087_social",
    ],
    "Information_Technology": [
        "information technology",
        "89_information", "402",
    ],
    "Hindi": [
        "hindi b", "hindi course b",
        "085_hindi",
    ],
}

# ─── Source URLs ──────────────────────────────────────────────────────────────
NEW_SITE_QP_URL = "https://www.cbse.gov.in/cbsenew/question-paper.html"
NEW_SITE_MS_URL = "https://www.cbse.gov.in/cbsenew/marking-scheme.html"
OLD_SITE_HUB    = "https://www.cbse.gov.in/newsite_old/examination.html"
SQP_ARCHIVE     = "https://cbseacademic.nic.in/sqp_archive.html"

# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("cbse_scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def normalize(s: str) -> str:
    """Lowercase + collapse whitespace."""
    return re.sub(r"\s+", " ", s.lower().strip())


def subject_matches(subject: str, text: str) -> bool:
    """Return True if any keyword for `subject` appears in `text`."""
    t = normalize(text)
    return any(normalize(kw) in t for kw in SUBJECTS[subject])


def is_downloadable(url: str) -> bool:
    """True for .pdf, .zip, .rar URLs."""
    return Path(urlparse(url).path).suffix.lower() in (".pdf", ".zip", ".rar")


def safe_filename(url: str) -> str:
    """Extract the filename portion from a URL."""
    return unquote(Path(urlparse(url).path).name) or "file"


def fix_url(url: str) -> str:
    """Percent-encode spaces and other unsafe characters in URL paths."""
    parts = urlparse(url)
    fixed = parts._replace(path=quote(parts.path, safe="/:@!$&'()*+,;="))
    return fixed.geturl()


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_session() -> requests.Session:
    """Return a session with browser-like headers."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    })
    return s


def fetch_page(session: requests.Session, url: str) -> BeautifulSoup | None:
    """
    GET a URL and return BeautifulSoup, or None on any error.
    Retries up to MAX_RETRIES times with exponential back-off.
    """
    url = fix_url(url)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 404:
                log.debug("404: %s", url)
                return None
            r.raise_for_status()
            return BeautifulSoup(r.content, "lxml")
        except requests.exceptions.Timeout:
            log.warning("Timeout (%d/%d): %s", attempt, MAX_RETRIES, url)
        except requests.exceptions.ConnectionError as exc:
            log.warning("Connection error (%d/%d): %s — %s", attempt, MAX_RETRIES, url, exc)
        except requests.exceptions.HTTPError as exc:
            log.warning("HTTP error: %s — %s", url, exc)
            return None   # no point retrying 4xx errors
        if attempt < MAX_RETRIES:
            time.sleep(REQUEST_DELAY * attempt)
    return None


def download_file(
    session: requests.Session,
    url: str,
    dest_path: Path,
    dry_run: bool = False,
) -> bool:
    """
    Download `url` to `dest_path`.
    • If URL is a .zip, the archive is extracted into dest_path's parent folder.
    • Skips if the file (or the extracted folder) already exists and is non-empty.
    • Returns True on success or skip, False on failure.
    """
    url = fix_url(url)
    is_zip = url.lower().endswith((".zip", ".rar"))

    # Already done?
    if is_zip:
        if dest_path.parent.exists() and any(dest_path.parent.iterdir()):
            log.debug("Already extracted, skipping: %s", dest_path.parent)
            return True
    elif dest_path.exists():
        log.debug("Already exists, skipping: %s", dest_path)
        return True

    if dry_run:
        log.info("[DRY-RUN] → %s", dest_path)
        return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=TIMEOUT, stream=True)
            r.raise_for_status()
            content = r.content

            if is_zip:
                _extract_zip(content, dest_path.parent, url)
            else:
                with open(dest_path, "wb") as f:
                    f.write(content)
            log.info("✓  %s", dest_path)
            return True

        except requests.exceptions.Timeout:
            log.warning("Timeout on attempt %d/%d: %s", attempt, MAX_RETRIES, url)
        except requests.exceptions.HTTPError as exc:
            code = exc.response.status_code
            log.warning("HTTP %d: %s", code, url)
            if code in (403, 404):
                return False   # no retry for auth/not-found errors
        except OSError as exc:
            log.error("File write error: %s — %s", dest_path, exc)
            return False
        except Exception as exc:
            log.warning("Unexpected error (%d/%d): %s — %s", attempt, MAX_RETRIES, url, exc)

        if attempt < MAX_RETRIES:
            time.sleep(REQUEST_DELAY * attempt)

    if dest_path.exists():
        dest_path.unlink()   # clean up partial file
    return False


def _extract_zip(data: bytes, dest_dir: Path, source_url: str):
    """Extract a ZIP archive into dest_dir. Falls back to raw save for bad ZIPs."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            z.extractall(dest_dir)
        log.info("✓  ZIP extracted → %s", dest_dir)
    except zipfile.BadZipFile:
        fname = safe_filename(source_url) or "download.bin"
        with open(dest_dir / fname, "wb") as f:
            f.write(data)
        log.warning("Bad ZIP saved raw: %s", dest_dir / fname)


# ═══════════════════════════════════════════════════════════════════════════════
# PATH BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def dest_for(
    subject: str,
    year: int,
    paper_type: str,   # "Main", "Compartment", "Sample_Paper"
    file_kind: str,    # "Question_Paper" or "Marking_Scheme"
    filename: str,
) -> Path:
    """
    Build the full destination path, e.g.:
      CBSE_Papers/Mathematics/2024/Main/Question_Paper/Mathematics_Standard.zip
    """
    return BASE_OUTPUT_DIR / subject / str(year) / paper_type / file_kind / filename


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE A — cbse.gov.in/cbsenew  (2022–2026)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Both QP and MS pages contain HTML tables like:
#   <h2>Question Paper for Class X Examination 2025</h2>
#   <table>
#     <tr><td>MATHEMATICS STANDARD</td><td><a href=".../2025/X/041_Mathematics_Standard.zip">Download</a></td>...
#   </table>
#
# Compartment rows are under headings containing "Compartment" and use
# year suffixes like "2025-COMPTT" in the URL path.

def _parse_new_site_page(
    soup: BeautifulSoup,
    base_url: str,
    target_year: int,
) -> list[dict]:
    """
    Extract all Class X download rows for target_year from the new-site page.
    Returns list of dicts: {subject_text, url, exam_type}
    """
    if soup is None:
        return []

    results = []
    current_class = None    # "X" | "XII" | None
    current_exam  = "Main"  # "Main" | "Compartment"
    year_in_scope = False

    for tag in soup.find_all(True):
        name = tag.name
        if name not in ("h2", "h3", "h4", "tr"):
            continue

        text = tag.get_text(separator=" ", strip=True)
        tl   = text.lower()

        # ── Heading: detect class and year context ──────────────────────────
        if name in ("h2", "h3", "h4"):
            # Reset scope whenever we see a different year
            if re.search(r"\b20\d\d\b", text):
                found_year = int(re.search(r"\b(20\d\d)\b", text).group(1))
                year_in_scope = (found_year == target_year)
                current_exam  = "Compartment" if "compartment" in tl else "Main"
            if "class x " in tl or "class x\n" in tl or tl.endswith("class x"):
                current_class = "X"
            elif "class xii" in tl:
                current_class = "XII"
            continue

        # ── Table row: only process Class X rows in scope ──────────────────
        if not year_in_scope or current_class != "X":
            continue

        cells = tag.find_all("td")
        if not cells:
            continue

        subject_text = cells[0].get_text(strip=True)

        for a in tag.find_all("a", href=True):
            href    = a["href"].strip()
            abs_url = urljoin(base_url, href) if not href.startswith("http") else href

            # Class X URLs contain "/X/" in the path
            if "/X/" not in abs_url and "/x/" not in abs_url.lower():
                continue
            if not is_downloadable(abs_url):
                continue

            results.append({
                "subject_text": subject_text,
                "url": abs_url,
                "exam_type": current_exam,
            })

    return results


def scrape_source_a(
    session, years: list[int], subjects: list[str], dry_run: bool, stats: dict
):
    log.info("═" * 62)
    log.info("  SOURCE A — cbse.gov.in/cbsenew  (2022–2026)")
    log.info("═" * 62)

    qp_soup = fetch_page(session, NEW_SITE_QP_URL);  time.sleep(REQUEST_DELAY)
    ms_soup = fetch_page(session, NEW_SITE_MS_URL);  time.sleep(REQUEST_DELAY)

    for year in sorted(years):
        if not (2022 <= year <= 2026):
            continue
        log.info("  ── Year %d", year)

        qp_rows = _parse_new_site_page(qp_soup, NEW_SITE_QP_URL, year)
        ms_rows = _parse_new_site_page(ms_soup, NEW_SITE_MS_URL, year)
        log.info("     QP links found: %d   MS links found: %d",
                 len(qp_rows), len(ms_rows))

        for subject in subjects:
            for row in qp_rows:
                if not subject_matches(subject, row["subject_text"]):
                    continue
                fname = safe_filename(row["url"])
                dest  = dest_for(subject, year, row["exam_type"], "Question_Paper", fname)
                ok    = download_file(session, row["url"], dest, dry_run)
                stats["downloaded" if ok else "failed"] += 1
                time.sleep(REQUEST_DELAY)

            for row in ms_rows:
                if not subject_matches(subject, row["subject_text"]):
                    continue
                fname = safe_filename(row["url"])
                dest  = dest_for(subject, year, row["exam_type"], "Marking_Scheme", fname)
                ok    = download_file(session, row["url"], dest, dry_run)
                stats["downloaded" if ok else "failed"] += 1
                time.sleep(REQUEST_DELAY)


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE B — cbse.gov.in/newsite_old  (2013–2021)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Hub page (examination.html) lists links like:
#   Question Papers for Examination 2020: [Class X link] | [Class XII link]
#   Marking Scheme for Examination 2020:  [Class X link] | [Class XII link]
#   (plus Compartment variants)
#
# Class X sub-pages are flat lists of  <a href="...pdf/zip">SUBJECT</a>.

def _old_site_year_urls(soup: BeautifulSoup, year: int) -> dict:
    """
    Parse the hub page for a given year and return a dict of
    {role: url}  where role is one of:
      qp_main, ms_main, qp_comptt, ms_comptt
    """
    urls = {}
    if soup is None:
        return urls

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        txt  = a.get_text(strip=True).lower()
        # Only Class X links whose href contains the year
        if str(year) not in href:
            continue
        # Must be Class X (not XII) — look at sibling text context
        parent_text = normalize(a.parent.get_text(separator=" "))
        if "class xii" in parent_text or "class 12" in parent_text:
            continue
        if "class x" not in parent_text and "class-x" not in parent_text:
            # Try the href itself: qp10-2019 → Class X
            if "10-" not in href and "qp10" not in href and "ms10" not in href:
                continue

        abs_url = urljoin(OLD_SITE_HUB, href)
        if "comptt" in href.lower() or "compartment" in href.lower():
            if "qp" in href.lower() or "question" in txt:
                urls["qp_comptt"] = abs_url
            else:
                urls["ms_comptt"] = abs_url
        else:
            if "qp" in href.lower() or "question" in txt:
                urls["qp_main"] = abs_url
            else:
                urls["ms_main"] = abs_url

    return urls


def _parse_old_sub_page(
    soup: BeautifulSoup,
    base_url: str,
    subjects: list[str],
) -> list[dict]:
    """
    Old sub-pages are simple lists of <a href=".pdf/.zip">SUBJECT</a>.
    Returns list of {subject, url}.
    """
    results = []
    if soup is None:
        return results
    for a in soup.find_all("a", href=True):
        href         = a["href"].strip()
        subject_text = a.get_text(strip=True)
        abs_url      = urljoin(base_url, href)
        if not is_downloadable(abs_url):
            continue
        for subject in subjects:
            if subject_matches(subject, subject_text):
                results.append({"subject": subject, "url": abs_url})
    return results


def scrape_source_b(
    session, years: list[int], subjects: list[str], dry_run: bool, stats: dict
):
    log.info("═" * 62)
    log.info("  SOURCE B — cbse.gov.in/newsite_old  (2013–2021)")
    log.info("═" * 62)

    hub_soup = fetch_page(session, OLD_SITE_HUB);  time.sleep(REQUEST_DELAY)

    for year in sorted(years):
        if not (2013 <= year <= 2021):
            continue
        log.info("  ── Year %d", year)

        year_urls = _old_site_year_urls(hub_soup, year)
        if not year_urls:
            # Fallback: construct URLs directly using the known pattern
            year_urls = {
                "qp_main":   f"https://www.cbse.gov.in/curric~1/qpms{year}/qp10-{year}.html",
                "ms_main":   f"https://www.cbse.gov.in/curric~1/qpms{year}/ms10-{year}.html",
                "qp_comptt": f"https://www.cbse.gov.in/curric~1/qpms{year}/comptt/qp10-{year}.html",
                "ms_comptt": f"https://www.cbse.gov.in/curric~1/qpms{year}/comptt/ms10-{year}.html",
            }

        role_map = {
            "qp_main":   ("Main",        "Question_Paper"),
            "ms_main":   ("Main",        "Marking_Scheme"),
            "qp_comptt": ("Compartment", "Question_Paper"),
            "ms_comptt": ("Compartment", "Marking_Scheme"),
        }

        for role, (exam_type, file_kind) in role_map.items():
            url = year_urls.get(role)
            if not url:
                continue
            soup = fetch_page(session, url);  time.sleep(REQUEST_DELAY)
            rows = _parse_old_sub_page(soup, url, subjects)
            log.info("     %s/%s: %d links", exam_type, file_kind, len(rows))
            for row in rows:
                fname = safe_filename(row["url"])
                dest  = dest_for(row["subject"], year, exam_type, file_kind, fname)
                ok    = download_file(session, row["url"], dest, dry_run)
                stats["downloaded" if ok else "failed"] += 1
                time.sleep(REQUEST_DELAY)


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE C — cbseacademic.nic.in  Sample Papers (SQP + MS)  2014–2026
# ═══════════════════════════════════════════════════════════════════════════════
#
# sqp_archive.html lists year-specific pages like:
#   SQP 2024-2025 → SQP_CLASSX_2024-25.html
#   SQP 2018-2019 → SQP_CLASSX_2018_19.html
#
# Each year page has a table:  Subject | SQP link | MS link

def _sqp_page_url(exam_year: int) -> str:
    """Return the SQP Class X URL for the examination year."""
    prev = exam_year - 1
    short = str(exam_year)[2:]
    if exam_year >= 2022:
        return f"https://cbseacademic.nic.in/SQP_CLASSX_{prev}-{short}.html"
    return f"https://cbseacademic.nic.in/SQP_CLASSX_{prev}_{short}.html"


def _parse_sqp_page(
    soup: BeautifulSoup,
    base_url: str,
    subjects: list[str],
) -> list[dict]:
    """
    SQP pages contain Subject | SQP link | MS link tables.
    Returns list of {subject, sqp_url, ms_url}.
    """
    results = []
    if soup is None:
        return results

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        subject_text = cells[0].get_text(strip=True)
        links        = row.find_all("a", href=True)

        sqp_url_found = ms_url_found = None
        for a in links:
            href    = a["href"].strip()
            txt     = a.get_text(strip=True).upper()
            abs_url = urljoin(base_url, href) if not href.startswith("http") else href
            if not is_downloadable(abs_url):
                continue
            if "MS" in txt:
                ms_url_found = abs_url
            elif "SQP" in txt or "QP" in txt:
                sqp_url_found = abs_url
            else:
                # Position-based: first = SQP, second = MS
                if sqp_url_found is None:
                    sqp_url_found = abs_url
                else:
                    ms_url_found = abs_url

        if not sqp_url_found and not ms_url_found:
            continue

        for subject in subjects:
            if subject_matches(subject, subject_text):
                results.append({
                    "subject":   subject,
                    "sqp_url":   sqp_url_found,
                    "ms_url":    ms_url_found,
                })

    return results


def scrape_source_c(
    session, years: list[int], subjects: list[str], dry_run: bool, stats: dict
):
    log.info("═" * 62)
    log.info("  SOURCE C — cbseacademic.nic.in  Sample Papers  (2014–2026)")
    log.info("═" * 62)

    for year in sorted(years):
        if not (2014 <= year <= 2026):
            continue
        url = _sqp_page_url(year)
        log.info("  ── Year %d  → %s", year, url)

        soup = fetch_page(session, url);  time.sleep(REQUEST_DELAY)
        if soup is None:
            log.warning("     No SQP page found for %d", year)
            continue

        rows = _parse_sqp_page(soup, url, subjects)
        log.info("     Found %d subject rows", len(rows))

        for row in rows:
            subject = row["subject"]

            if row["sqp_url"]:
                fname = safe_filename(row["sqp_url"])
                dest  = dest_for(subject, year, "Sample_Paper", "Question_Paper", fname)
                ok    = download_file(session, row["sqp_url"], dest, dry_run)
                stats["downloaded" if ok else "failed"] += 1
                time.sleep(REQUEST_DELAY)

            if row["ms_url"]:
                fname = safe_filename(row["ms_url"])
                dest  = dest_for(subject, year, "Sample_Paper", "Marking_Scheme", fname)
                ok    = download_file(session, row["ms_url"], dest, dry_run)
                stats["downloaded" if ok else "failed"] += 1
                time.sleep(REQUEST_DELAY)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def run(years: list[int], subjects: list[str], dry_run: bool) -> dict:
    session = build_session()
    stats   = {"downloaded": 0, "failed": 0}

    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("   CBSE Class 10 Paper Downloader  — starting")
    log.info("   Years   : %s → %s", min(years), max(years))
    log.info("   Subjects: %s", ", ".join(subjects))
    log.info("   Output  : %s", BASE_OUTPUT_DIR.resolve())
    log.info("   Dry run : %s", dry_run)
    log.info("╚══════════════════════════════════════════════════════════╝")

    new_years = [y for y in years if 2022 <= y <= 2026]
    old_years = [y for y in years if 2013 <= y <= 2021]
    sqp_years = [y for y in years if 2014 <= y <= 2026]

    if new_years:
        scrape_source_a(session, new_years, subjects, dry_run, stats)
    if old_years:
        scrape_source_b(session, old_years, subjects, dry_run, stats)
    if sqp_years:
        scrape_source_c(session, sqp_years, subjects, dry_run, stats)

    return stats


def parse_args():
    p = argparse.ArgumentParser(
        description="Download CBSE Class 10 question papers & marking schemes."
    )
    p.add_argument("--years", nargs="+", type=int,
                   default=list(range(2013, 2027)),
                   help="Years to download (default: 2013–2026)")
    p.add_argument("--subjects", nargs="+",
                   choices=list(SUBJECTS.keys()),
                   default=list(SUBJECTS.keys()),
                   help="Subjects (default: all 6)")
    p.add_argument("--output", type=Path, default=BASE_OUTPUT_DIR,
                   help="Root output folder (default: CBSE_Papers/)")
    p.add_argument("--dry-run", action="store_true",
                   help="Discover and list files without downloading")
    p.add_argument("--delay", type=float, default=REQUEST_DELAY,
                   help=f"Seconds between requests (default: {REQUEST_DELAY})")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    BASE_OUTPUT_DIR = args.output
    REQUEST_DELAY   = args.delay

    stats = run(
        years    = sorted(set(args.years)),
        subjects = args.subjects,
        dry_run  = args.dry_run,
    )

    print("\n" + "═" * 62)
    print("  DOWNLOAD COMPLETE")
    print("═" * 62)
    print(f"  Successfully saved : {stats['downloaded']}")
    print(f"  Failed / not found : {stats['failed']}")
    print("═" * 62)
    print(f"\n  Output folder : {BASE_OUTPUT_DIR.resolve()}")
    print(f"  Activity log  : cbse_scraper.log\n")

    with open("download_summary.json", "w") as f:
        json.dump(stats, f, indent=2)
    print("  JSON summary  : download_summary.json")
