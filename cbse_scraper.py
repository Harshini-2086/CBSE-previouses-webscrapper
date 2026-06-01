"""
CBSE Class 10 Paper Downloader  v3.0
=====================================
Downloads question papers + marking schemes for Class 10 core subjects
across three CBSE portals:

  SOURCE A  2022–2026  cbse.gov.in/cbsenew/question-paper.html  &  marking-scheme.html
  SOURCE B  2013–2021  cbse.gov.in/newsite_old/examination.html  (hub → sub-pages)
  SOURCE C  2014–2026  cbseacademic.nic.in/sqp_archive.html      (Sample Question Papers)

Subjects (with official CBSE codes):
  184  English Language & Literature
  085  Hindi Course-B
  041  Mathematics Standard
  241  Mathematics Basic
  086  Science
  087  Social Science
  402  Information Technology

Usage:
  python cbse_scraper.py                            # all subjects, 2013–2026
  python cbse_scraper.py --years 2022 2023 2024     # specific years
  python cbse_scraper.py --subjects Mathematics_Standard Mathematics_Basic
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
import shutil
import tempfile

import requests
from bs4 import BeautifulSoup

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BASE_OUTPUT_DIR = Path("CBSE_Papers")
REQUEST_DELAY   = 1.5
TIMEOUT         = 30
MAX_RETRIES     = 3

# ─── Subject definitions ───────────────────────────────────────────────────────
# Each entry has:
#   "code" : official CBSE subject code — PRIMARY match criterion
#   "name" : official subject name      — SECONDARY (fallback) match criterion
#
# Matching priority (see subject_matches()):
#   1. Code found as a whole token in the text OR in the file URL  → match
#   2. Normalised subject name found anywhere in the text           → match
#   3. Neither found                                                → no match
#
# WHY codes first?  CBSE pages and file names consistently embed the three-digit
# code (e.g. "041_Mathematics_Standard.zip", "Subject Code: 041") while the
# human-readable subject name varies in punctuation and spacing across pages.

SUBJECTS: dict[str, dict] = {
    "English_Language_Literature": {
        "code": "184",
        "name": "english language & literature",
    },
    "Hindi_Course_B": {
        "code": "085",
        "name": "hindi course-b",
    },
    "Mathematics_Standard": {
        "code": "041",
        "name": "mathematics standard",
    },
    "Mathematics_Basic": {
        "code": "241",
        "name": "mathematics basic",
    },
    "Science": {
        "code": "086",
        "name": "science",
    },
    "Social_Science": {
        "code": "087",
        "name": "social science",
    },
    "Information_Technology": {
        "code": "402",
        "name": "information technology",
    },
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
    """Lowercase, collapse whitespace, normalise common punctuation variants."""
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    # Normalise "lang. & lit.", "lang & lit", "language and literature" → uniform
    s = s.replace("&amp;", "&").replace(" and ", " & ")
    return s


# def subject_matches(subject_key: str, text: str, url: str = "") -> bool:
#     """
#     Return True when the given text (and optionally the file URL) belongs to
#     `subject_key`.

#     Priority:
#       1. Subject CODE found as a whole token in `text` OR in the URL path.
#          A "whole token" means the code is surrounded by non-digit characters
#          (prevents '087' matching inside '1087').
#       2. Normalised subject NAME is a substring of normalised `text`.

#     This two-tier approach avoids false positives from broad keyword lists
#     while still handling pages that omit the code.
#     """
#     cfg  = SUBJECTS[subject_key]
#     code = cfg["code"]
#     name = cfg["name"]

#     # ── Tier 1: code match ──────────────────────────────────────────────────
#     # Check in display text
#     code_pattern = rf"(?<!\d){re.escape(code)}(?!\d)"
#     if re.search(code_pattern, text):
#         return True
#     # Check in URL path (e.g. "041_Mathematics_Standard.zip")
#     if url and re.search(code_pattern, urlparse(url).path):
#         return True

#     # ── Tier 2: name match (fallback) ───────────────────────────────────────
#     if normalize(name) in normalize(text):
#         return True

#     return False

def subject_matches(subject_key: str, text: str, url: str = "", year: int = None) -> bool:
    """
    Return True when the given text (and optionally the file URL) belongs to
    `subject_key`. Maps pre-2020 generic Mathematics to Mathematics_Standard.
    """
    cfg  = SUBJECTS[subject_key]
    code = cfg["code"]
    name = cfg["name"]

    norm_text = normalize(text)
    norm_url  = normalize(urlparse(url).path) if url else ""

    # ── Language & Special Version Filter ───────────────────────────────────
    excluded_keywords = ["blind", "vi candidate", "visually", "urdu", "punjabi", "sanskrit", "bengali"]
    if subject_key != "Hindi_Course_B":
        excluded_keywords.append("hindi")
    if any(kw in norm_text or kw in norm_url for kw in excluded_keywords):
        return False

    # ── Pre-2020 Mathematics Routing ──────────────────────────────────────
    if year and year < 2020:
        if subject_key == "Mathematics_Basic":
            return False  # Basic did not exist before 2020
        if subject_key == "Mathematics_Standard":
            # Prior to 2020, it was just called "Mathematics" or "Maths"
            if "mathematics" in norm_text or "math" in norm_text:
                return True

    # ── Tier 1: code match ──────────────────────────────────────────────────
    code_pattern = rf"(?<!\d){re.escape(code)}(?!\d)"
    if re.search(code_pattern, text):
        return True
    if url and re.search(code_pattern, urlparse(url).path):
        return True

    # ── Tier 2: name match (fallback) ───────────────────────────────────────
    norm_name = normalize(name)
    if norm_name in norm_text:
        if subject_key == "Science":
            excluded_prefixes = ["home", "data", "computer", "environmental","Foundational Skills for"]
            if any(prefix in norm_text for prefix in excluded_prefixes):
                return False
        return True

    return False

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
            return None
        if attempt < MAX_RETRIES:
            time.sleep(REQUEST_DELAY * attempt)
    return None


def download_file(
    session: requests.Session,
    url: str,
    dest_path: Path,
    dry_run: bool = False,
) -> bool:
    url    = fix_url(url)
    is_zip = url.lower().endswith((".zip", ".rar"))

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
                return False
        except OSError as exc:
            log.error("File write error: %s — %s", dest_path, exc)
            return False
        except Exception as exc:
            log.warning("Unexpected error (%d/%d): %s — %s", attempt, MAX_RETRIES, url, exc)

        if attempt < MAX_RETRIES:
            time.sleep(REQUEST_DELAY * attempt)

    if dest_path.exists():
        dest_path.unlink()
    return False


def _extract_zip(data: bytes, dest_dir: Path, source_url: str):
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                z.extractall(tmp_path)
                
                extracted_items = list(tmp_path.iterdir())
                
                dest_dir.mkdir(parents=True, exist_ok=True)
                
                if len(extracted_items) == 1 and extracted_items[0].is_dir():
                    for item in extracted_items[0].iterdir():
                        shutil.move(str(item), str(dest_dir / item.name))
                else:
                    for item in extracted_items:
                        shutil.move(str(item), str(dest_dir / item.name))
                        
        log.info("✓  ZIP extracted → %s", dest_dir)
    except zipfile.BadZipFile:
        fname = safe_filename(source_url) or "download.bin"
        dest_dir.mkdir(parents=True, exist_ok=True)
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
    return BASE_OUTPUT_DIR / subject / str(year) / paper_type / file_kind / filename


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE A — cbse.gov.in/cbsenew  (2022–2026)
# ═══════════════════════════════════════════════════════════════════════════════
#
# FIX — Compartment Marking Schemes (Issue 2):
# ─────────────────────────────────────────────
# The original code only updated `current_exam` inside heading tags when it
# encountered the word "compartment". However, the MS page uses headings like:
#
#   "Answer Key / Marking Scheme for Compartment Examination 2024"
#   "Marking Scheme (Compartment) Class X 2023"
#
# …which the old regex never matched because it only looked for "compartment"
# inside headings that also contained a 4-digit year. We now detect compartment
# context from ANY heading or section-divider tag (h2/h3/h4/strong/b) that
# mentions "compartment", regardless of whether it also contains a year.
# The year-scope flag is updated independently, so both can be set correctly.

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

    results      = []
    current_class = None    # "X" | "XII" | None
    current_exam  = "Main"  # "Main" | "Compartment"
    year_in_scope = False

    # Broaden the tag set: include <strong> and <b> as section dividers because
    # some CBSE pages use bold inline text instead of heading tags to label
    # compartment sections.
    HEADING_TAGS = {"h2", "h3", "h4", "strong", "b"}

    for tag in soup.find_all(True):
        name = tag.name
        if name not in HEADING_TAGS and name != "tr":
            continue

        text = tag.get_text(separator=" ", strip=True)
        tl   = text.lower()

        # ── Heading / section-divider logic ────────────────────────────────
        if name in HEADING_TAGS:
            # --- Year detection ---
            year_match = re.search(r"\b(20\d{2})\b", text)
            if year_match:
                found_year    = int(year_match.group(1))
                year_in_scope = (found_year == target_year)

            # --- Exam-type detection (independent of year presence) ---
            # Any heading mentioning "compartment" switches context; any heading
            # mentioning "examination" WITHOUT "compartment" resets to Main.
            # This correctly handles both:
            #   "Marking Scheme Compartment 2024"  → Compartment
            #   "Question Paper Examination 2024"  → Main
            if "compartment" in tl:
                current_exam = "Compartment"
            elif any(kw in tl for kw in ("examination", "question paper", "marking scheme", "answer key")):
                # Only reset to Main if the heading doesn't also say compartment
                current_exam = "Main"

            # --- Class detection ---
            if re.search(r"\bclass\s*x\b(?!\s*i)", tl):   # "class x" but not "class xi/xii"
                current_class = "X"
            elif re.search(r"\bclass\s*x(ii|i)\b", tl):
                current_class = "XII"
            continue

        # ── Table row processing ────────────────────────────────────────────
        if not year_in_scope or current_class != "X":
            continue

        cells = tag.find_all("td")
        if not cells:
            continue

        subject_text = cells[0].get_text(strip=True)

        for a in tag.find_all("a", href=True):
            href    = a["href"].strip()
            abs_url = urljoin(base_url, href) if not href.startswith("http") else href

            if "/X/" not in abs_url and "/x/" not in abs_url.lower():
                continue
            if not is_downloadable(abs_url):
                continue

            # Re-check exam type from the URL itself as a safety net:
            # CBSE compartment files typically contain "COMPTT" in the path.
            url_exam = current_exam
            if "comptt" in abs_url.lower() or "compartment" in abs_url.lower():
                url_exam = "Compartment"

            results.append({
                "subject_text": subject_text,
                "url": abs_url,
                "exam_type": url_exam,
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
                # Inside the QP loop
                if not subject_matches(subject, row["subject_text"], row["url"], year=year):
                    continue
                fname = safe_filename(row["url"])
                dest  = dest_for(subject, year, row["exam_type"], "Question_Paper", fname)
                ok    = download_file(session, row["url"], dest, dry_run)
                stats["downloaded" if ok else "failed"] += 1
                time.sleep(REQUEST_DELAY)

            for row in ms_rows:
                # Inside the MS loop
                if not subject_matches(subject, row["subject_text"], row["url"], year=year):
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
# FIX — Compartment Marking Schemes (Issue 2):
# ─────────────────────────────────────────────
# The old code classified links using a single `if "qp" in href` check, which
# meant many compartment MS links (whose hrefs contain neither "qp" nor "ms"
# explicitly) defaulted silently to qp_comptt and overwrote the real QP URL,
# or were dropped entirely.
#
# New approach: scan the link's TEXT first (which is the most reliable signal
# since CBSE labels it "Marking Scheme" or "Answer Key"), then fall back to
# href keywords, then fall back to sibling-text context.

def _classify_link(href: str, link_text: str, parent_text: str) -> tuple[str, str]:
    """
    Return (exam_type, file_kind) for a hub-page link.
    exam_type : "Main" | "Compartment"
    file_kind : "Question_Paper" | "Marking_Scheme"
    """
    hl  = href.lower()
    tl  = link_text.lower()
    pl  = parent_text.lower()

    # ── exam type ───────────────────────────────────────────────────────────
    is_comptt = (
        "comptt" in hl
        or "compartment" in hl
        or "compartment" in tl
        or "compartment" in pl
    )
    exam_type = "Compartment" if is_comptt else "Main"

    # ── file kind ───────────────────────────────────────────────────────────
    # Check link text first — it is the most explicit signal.
    if any(kw in tl for kw in ("marking scheme", "answer key", "ms")):
        file_kind = "Marking_Scheme"
    elif any(kw in tl for kw in ("question paper", "qp")):
        file_kind = "Question_Paper"
    # Fall back to href keywords
    elif any(kw in hl for kw in ("ms10", "ms-10", "/ms/")):
        file_kind = "Marking_Scheme"
    elif any(kw in hl for kw in ("qp10", "qp-10", "/qp/")):
        file_kind = "Question_Paper"
    # Fall back to surrounding paragraph text
    elif any(kw in pl for kw in ("marking scheme", "answer key")):
        file_kind = "Marking_Scheme"
    else:
        file_kind = "Question_Paper"   # conservative default

    return exam_type, file_kind


def _old_site_year_urls(soup: BeautifulSoup, year: int) -> dict:
    """
    Parse the hub page for a given year.
    Returns a dict keyed by  "<exam_type>_<file_kind>"  →  URL, e.g.:
      {"Main_Question_Paper": "...", "Compartment_Marking_Scheme": "...", ...}
    """
    urls: dict[str, str] = {}
    if soup is None:
        return urls

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if str(year) not in href:
            continue

        # Skip Class XII links
        parent_text = normalize(a.parent.get_text(separator=" "))
        if "class xii" in parent_text or "class 12" in parent_text:
            continue
        # Must look like a Class X link
        is_class_x = (
            "class x" in parent_text
            or "class-x" in parent_text
            or "10-" in href
            or "qp10" in href.lower()
            or "ms10" in href.lower()
        )
        if not is_class_x:
            continue

        abs_url   = urljoin(OLD_SITE_HUB, href)
        link_text = a.get_text(strip=True)
        exam_type, file_kind = _classify_link(href, link_text, parent_text)
        key = f"{exam_type}_{file_kind}"

        # Keep the first match for each key (most prominent link wins)
        if key not in urls:
            urls[key] = abs_url
            log.debug("  Hub link  %-35s → %s", key, abs_url)

    return urls


def _parse_old_sub_page(soup: BeautifulSoup, base_url: str, subjects: list[str], year: int) -> list[dict]:
    # ... inside the loop ...
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
            if subject_matches(subject, subject_text, abs_url, year=year):
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
            # Fallback: construct URLs using the known CBSE path pattern
            year_urls = {
                "Main_Question_Paper":        f"https://www.cbse.gov.in/curric~1/qpms{year}/qp10-{year}.html",
                "Main_Marking_Scheme":        f"https://www.cbse.gov.in/curric~1/qpms{year}/ms10-{year}.html",
                "Compartment_Question_Paper": f"https://www.cbse.gov.in/curric~1/qpms{year}/comptt/qp10-{year}.html",
                "Compartment_Marking_Scheme": f"https://www.cbse.gov.in/curric~1/qpms{year}/comptt/ms10-{year}.html",
            }

        # Map new key format → (paper_type, file_kind) folder names
        role_map = {
            "Main_Question_Paper":        ("Main",        "Question_Paper"),
            "Main_Marking_Scheme":        ("Main",        "Marking_Scheme"),
            "Compartment_Question_Paper": ("Compartment", "Question_Paper"),
            "Compartment_Marking_Scheme": ("Compartment", "Marking_Scheme"),
        }

        for key, (paper_type, file_kind) in role_map.items():
            url = year_urls.get(key)
            if not url:
                log.debug("  No URL for %s %d", key, year)
                continue
            soup = fetch_page(session, url);  time.sleep(REQUEST_DELAY)
            rows = _parse_old_sub_page(soup, url, subjects, year=year)
            log.info("     %s: %d links", key, len(rows))
            for row in rows:
                fname = safe_filename(row["url"])
                dest  = dest_for(row["subject"], year, paper_type, file_kind, fname)
                ok    = download_file(session, row["url"], dest, dry_run)
                stats["downloaded" if ok else "failed"] += 1
                time.sleep(REQUEST_DELAY)


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE C — cbseacademic.nic.in  Sample Papers (SQP + MS)  2014–2026
# ═══════════════════════════════════════════════════════════════════════════════

def _sqp_page_url(exam_year: int) -> str:
    prev  = exam_year - 1
    short = str(exam_year)[2:]
    if exam_year >= 2022:
        return f"https://cbseacademic.nic.in/SQP_CLASSX_{prev}-{short}.html"
    return f"https://cbseacademic.nic.in/SQP_CLASSX_{prev}_{short}.html"


def _parse_sqp_page(soup: BeautifulSoup, base_url: str, subjects: list[str], year: int) -> list[dict]:
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
                if sqp_url_found is None:
                    sqp_url_found = abs_url
                else:
                    ms_url_found = abs_url

        if not sqp_url_found and not ms_url_found:
            continue

        for subject in subjects:
            if subject_matches(subject, subject_text, year=year):
                results.append({
                    "subject": subject,
                    "sqp_url": sqp_url_found,
                    "ms_url":  ms_url_found,
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

        rows = _parse_sqp_page(soup, url, subjects, year=year)
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
    log.info("   CBSE Class 10 Paper Downloader  v3.0 — starting")
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
                   help="Subjects (default: all)")
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
