#!/usr/bin/env python3
"""
Research Paper Downloader Tool

Downloads research papers (PDFs) from journal websites.
Supports Nature, Cell, Science, and ACS publisher families.

Usage:
    python3 download_papers.py                          # all journals, today
    python3 download_papers.py --url "https://..."      # single journal
    python3 download_papers.py --date 2026-03-15        # specific date
    python3 download_papers.py --dry-run                # list without downloading
    python3 download_papers.py --install-schedule       # set up daily auto-run
    python3 download_papers.py --uninstall-schedule     # remove daily auto-run
"""

import argparse
import json
import logging
import os
import pickle
import plistlib
import random
import re
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
CONFIG_PATH = SCRIPT_DIR / "journal_configs.json"
TMP_DIR = PROJECT_DIR / ".tmp"
COOKIE_DIR = TMP_DIR / "cookies"
LOG_PATH = TMP_DIR / "paper_download.log"
LAUNCHD_LABEL = "com.wat.download-papers"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def setup_logging(verbose: bool = False):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt,
                        handlers=[
                            logging.StreamHandler(),
                            logging.FileHandler(LOG_PATH, encoding="utf-8"),
                        ])

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Browser manager
# ---------------------------------------------------------------------------
class BrowserManager:
    """Manages a Chrome instance via Selenium."""

    def __init__(self, headless: bool = True):
        self.driver = None
        self.headless = headless

    def start(self):
        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        # Use a persistent profile so Cloudflare clearance cookies survive across runs
        profile_dir = TMP_DIR / "chrome-profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        opts.add_argument(f"--user-data-dir={profile_dir}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=opts)
        # Remove webdriver flag
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        logging.debug(f"Browser started (headless={self.headless})")
        return self.driver

    def stop(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
            logging.debug("Browser stopped")

    def load_cookies(self, domain: str):
        COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        cookie_file = COOKIE_DIR / f"{domain}.pkl"
        if cookie_file.exists():
            cookies = pickle.load(open(cookie_file, "rb"))
            for cookie in cookies:
                try:
                    self.driver.add_cookie(cookie)
                except Exception:
                    pass
            logging.debug(f"Loaded cookies for {domain}")

    def save_cookies(self, domain: str):
        COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        cookie_file = COOKIE_DIR / f"{domain}.pkl"
        pickle.dump(self.driver.get_cookies(), open(cookie_file, "wb"))
        logging.debug(f"Saved cookies for {domain}")

    def get_page(self, url: str, wait_seconds: int = 15) -> BeautifulSoup:
        """Navigate to URL, wait for content, and return parsed HTML."""
        domain = urlparse(url).netloc
        # Load cookies first by visiting the domain root, then navigate
        cookie_file = COOKIE_DIR / f"{domain}.pkl"
        if cookie_file.exists():
            self.driver.get(f"https://{domain}")
            self.load_cookies(domain)
        self.driver.get(url)
        # Wait for page, with Cloudflare challenge handling
        time.sleep(wait_seconds)
        source = self.driver.page_source
        if "challenge-platform" in source or "Just a moment" in source:
            if not self.headless:
                logging.info(f"Cloudflare challenge on {domain} — please solve it in the browser window...")
                # Wait up to 120s for user to solve challenge
                for _ in range(24):
                    time.sleep(5)
                    source = self.driver.page_source
                    if "challenge-platform" not in source and "Just a moment" not in source:
                        logging.info(f"Cloudflare challenge solved for {domain}")
                        break
                else:
                    logging.warning(f"Cloudflare challenge not solved for {domain} after 120s, skipping")
            else:
                logging.warning(f"Cloudflare challenge on {domain} — try running with --visible to solve manually")
        self._dismiss_cookie_banner()
        self.save_cookies(domain)
        return BeautifulSoup(self.driver.page_source, "lxml")

    def _dismiss_cookie_banner(self):
        """Try to click common cookie consent buttons."""
        selectors = [
            "button[id*='accept']",
            "button[class*='accept']",
            "button[data-action='accept']",
            "a[class*='agree']",
            "button[class*='consent']",
            "button[class*='cookie']",
            "#onetrust-accept-btn-handler",
            ".cc-accept",
        ]
        for sel in selectors:
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_displayed():
                    btn.click()
                    logging.debug(f"Dismissed cookie banner via {sel}")
                    time.sleep(1)
                    return
            except Exception:
                continue

    def transfer_cookies_to_session(self) -> requests.Session:
        """Create a requests.Session with the browser's cookies."""
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        })
        for cookie in self.driver.get_cookies():
            session.cookies.set(cookie["name"], cookie["value"],
                                domain=cookie.get("domain", ""))
        return session


# ---------------------------------------------------------------------------
# Article dataclass-like dict helper
# ---------------------------------------------------------------------------
def make_article(title: str, url: str, article_date: str, doi: str = "") -> dict:
    return {"title": title, "url": url, "date": article_date, "doi": doi}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
class BaseParser(ABC):
    """Abstract base for journal-specific parsers."""

    @abstractmethod
    def get_articles(self, soup: BeautifulSoup, page_url: str) -> list[dict]:
        """Return list of article dicts from a listing page."""
        ...

    def filter_by_date(self, articles: list[dict], target_date: date) -> list[dict]:
        """Keep only articles matching the target date."""
        result = []
        for a in articles:
            try:
                d = self._parse_date(a["date"])
                if d == target_date:
                    result.append(a)
            except (ValueError, TypeError):
                # If we can't parse the date, include the article (be permissive)
                logging.debug(f"Could not parse date '{a['date']}' for '{a['title']}', including anyway")
                result.append(a)
        return result

    def _parse_date(self, date_str: str) -> date:
        """Try multiple date formats after stripping common prefixes."""
        date_str = date_str.strip()
        # Strip known prefixes from publishers
        for prefix in ["First published:", "Publication Date(Web):",
                       "Published:", "Online:"]:
            if date_str.startswith(prefix):
                date_str = date_str[len(prefix):].strip()
        for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y",
                    "%b %d, %Y", "%B %-d, %Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        # Try regex fallback for "Month D, YYYY" with single-digit day
        m = re.match(r"(\w+)\s+(\d{1,2}),?\s+(\d{4})", date_str)
        if m:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)}, {m.group(3)}",
                                         "%B %d, %Y").date()
            except ValueError:
                pass
        raise ValueError(f"Cannot parse date: {date_str}")

    def get_pdf_url(self, driver, article_url: str) -> str | None:
        """Navigate to article page and find PDF link. Override per publisher."""
        return None


class NatureParser(BaseParser):
    """Parser for Nature/Springer journals."""

    def get_articles(self, soup: BeautifulSoup, page_url: str) -> list[dict]:
        articles = []
        base_url = "https://www.nature.com"

        # Nature uses <article> tags in article listings
        for item in soup.select("article"):
            title_el = item.select_one("h3 a, h2 a, a[data-track-action='view article']")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            url = urljoin(base_url, href)

            # Date from <time> element
            time_el = item.select_one("time")
            article_date = ""
            if time_el:
                article_date = time_el.get("datetime", time_el.get_text(strip=True))

            articles.append(make_article(title, url, article_date))

        logging.info(f"Nature parser found {len(articles)} articles on {page_url}")
        return articles

    def get_pdf_url(self, driver, article_url: str) -> str | None:
        # Nature PDF URLs follow pattern: article_url + .pdf
        return article_url + ".pdf"


class CellParser(BaseParser):
    """Parser for Cell Press / Elsevier journals."""

    def get_articles(self, soup: BeautifulSoup, page_url: str) -> list[dict]:
        articles = []
        base_url = "https://www.cell.com"

        for item in soup.select(".toc__item"):
            title_el = item.select_one("h3.toc__item__title a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            url = urljoin(base_url, href)

            date_el = item.select_one(".toc__item__date")
            article_date = ""
            if date_el:
                article_date = date_el.get_text(strip=True)

            articles.append(make_article(title, url, article_date))

        logging.info(f"Cell parser found {len(articles)} articles on {page_url}")
        return articles

    def get_pdf_url(self, driver, article_url: str) -> str | None:
        # Cell PDFs: replace /fulltext/ with /pdfExtended/
        if "/fulltext/" in article_url:
            return article_url.replace("/fulltext/", "/pdfExtended/")
        # Try appending /pdf
        return article_url.rstrip("/") + "/pdf"


class ScienceParser(BaseParser):
    """Parser for Science/AAAS journals."""

    def get_articles(self, soup: BeautifulSoup, page_url: str) -> list[dict]:
        articles = []
        base_url = "https://www.science.org"

        for item in soup.select(".card.border-bottom"):
            title_el = item.select_one("h3.article-title a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            url = urljoin(base_url, href)

            date_el = item.select_one("time")
            article_date = ""
            if date_el:
                article_date = date_el.get("datetime", date_el.get_text(strip=True))

            articles.append(make_article(title, url, article_date))

        logging.info(f"Science parser found {len(articles)} articles on {page_url}")
        return articles

    def get_pdf_url(self, driver, article_url: str) -> str | None:
        # Science PDFs: /doi/abs/... or /doi/full/... -> /doi/pdf/...
        for pattern in ["/doi/abs/", "/doi/full/", "/doi/"]:
            if pattern in article_url:
                return article_url.replace(pattern, "/doi/pdf/", 1)
        return article_url + "/pdf"


class ACSParser(BaseParser):
    """Parser for ACS Publications."""

    def get_articles(self, soup: BeautifulSoup, page_url: str) -> list[dict]:
        articles = []
        base_url = "https://pubs.acs.org"

        for item in soup.select(".issue-item"):
            title_el = item.select_one(".issue-item_title a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            url = urljoin(base_url, href)

            date_el = item.select_one(".pub-date-value")
            article_date = ""
            if date_el:
                article_date = date_el.get_text(strip=True)

            articles.append(make_article(title, url, article_date))

        logging.info(f"ACS parser found {len(articles)} articles on {page_url}")
        return articles

    def get_pdf_url(self, driver, article_url: str) -> str | None:
        # ACS PDFs: /doi/abs/... or /doi/... -> /doi/pdf/...
        if "/doi/abs/" in article_url:
            return article_url.replace("/doi/abs/", "/doi/pdf/")
        if "/doi/" in article_url and "/doi/pdf/" not in article_url:
            return article_url.replace("/doi/", "/doi/pdf/", 1)
        return article_url + "/pdf"


# ---------------------------------------------------------------------------
# RSS parser (no Selenium needed)
# ---------------------------------------------------------------------------
class RSSParser:
    """Fetches article listings from RSS feeds using plain HTTP requests."""

    # PDF URL construction rules per parser type
    PDF_RULES = {
        "nature": lambda url: url + ".pdf",
        "cell": lambda url: (url.replace("/fulltext/", "/pdfExtended/")
                             if "/fulltext/" in url else url.rstrip("/") + "/pdf"),
        "science": lambda url: (url.replace("/doi/abs/", "/doi/pdf/", 1)
                                if "/doi/abs/" in url
                                else url.replace("/doi/full/", "/doi/pdf/", 1)
                                if "/doi/full/" in url
                                else url.replace("/doi/", "/doi/pdf/", 1)
                                if "/doi/" in url and "/doi/pdf/" not in url
                                else url + "/pdf"),
        "acs": lambda url: (url.replace("/doi/abs/", "/doi/pdf/")
                            if "/doi/abs/" in url
                            else url.replace("/doi/", "/doi/pdf/", 1)
                            if "/doi/" in url and "/doi/pdf/" not in url
                            else url + "/pdf"),
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        })

    def fetch_articles(self, rss_url: str, parser_type: str) -> list[dict]:
        """Fetch and parse an RSS feed, returning article dicts."""
        try:
            resp = self.session.get(rss_url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logging.warning(f"RSS fetch failed for {rss_url}: {e}")
            return []

        soup = BeautifulSoup(resp.content, "lxml-xml")
        articles = []

        for item in soup.find_all("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            date_el = item.find("dc:date") or item.find("pubDate") or item.find("prism:coverDate")

            if not title_el or not link_el:
                continue

            title = title_el.get_text(strip=True)
            url = link_el.get_text(strip=True)
            article_date = date_el.get_text(strip=True) if date_el else ""

            articles.append(make_article(title, url, article_date))

        logging.info(f"RSS found {len(articles)} articles from {rss_url}")
        return articles

    def filter_by_date(self, articles: list[dict], target_date: date) -> list[dict]:
        """Keep only articles matching the target date."""
        result = []
        for a in articles:
            parsed = self._parse_date(a["date"])
            if parsed is None or parsed == target_date:
                result.append(a)
        return result

    def _parse_date(self, date_str: str) -> date | None:
        """Parse RSS date formats."""
        if not date_str:
            return None
        date_str = date_str.strip()
        # ISO format: 2026-03-13T00:00:00Z or 2026-03-13
        if date_str[:10].count("-") == 2:
            try:
                return date.fromisoformat(date_str[:10])
            except ValueError:
                pass
        # RFC 2822: Thu, 13 Mar 2026 00:00:00 GMT
        for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                    "%d %b %Y", "%d %B %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        logging.debug(f"Could not parse RSS date: {date_str}")
        return None

    def get_pdf_url(self, article_url: str, parser_type: str) -> str | None:
        """Construct PDF URL based on publisher rules."""
        # Strip query parameters before constructing PDF URL
        clean_url = article_url.split("?")[0]
        rule = self.PDF_RULES.get(parser_type)
        return rule(clean_url) if rule else None


# ---------------------------------------------------------------------------
# Parser registry
# ---------------------------------------------------------------------------
PARSERS: dict[str, BaseParser] = {
    "nature": NatureParser(),
    "cell": CellParser(),
    "science": ScienceParser(),
    "acs": ACSParser(),
}


# ---------------------------------------------------------------------------
# Paper downloader
# ---------------------------------------------------------------------------
class PaperDownloader:
    """Orchestrates scraping and downloading papers."""

    def __init__(self, config: dict, target_date: date, dry_run: bool = False,
                 headless: bool = True):
        self.config = config
        self.target_date = target_date
        self.dry_run = dry_run
        self.headless = headless
        self.output_dir = Path(config["output_base_dir"]).expanduser() / target_date.isoformat()
        self.browser = None  # Lazy init — only started if RSS fails
        self.rss_parser = RSSParser()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        })
        self.stats = {"downloaded": 0, "failed": 0, "skipped": 0}

    def _ensure_browser(self):
        """Start browser only when needed (Selenium fallback)."""
        if self.browser is None:
            self.browser = BrowserManager(headless=self.headless)
            self.browser.start()
            self.session = self.browser.transfer_cookies_to_session()

    def run(self, filter_url: str | None = None):
        """Main entry point: scrape listings and download PDFs."""
        journals = self.config["journals"]
        if filter_url:
            journals = [j for j in journals if j["url"] == filter_url]
            if not journals:
                logging.error(f"No journal config found for URL: {filter_url}")
                return

        if not self.dry_run:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            logging.info(f"Output directory: {self.output_dir}")

        try:
            for journal in journals:
                self._process_journal(journal)
                time.sleep(random.uniform(1, 3))
        finally:
            if self.browser:
                self.browser.stop()

        # Summary
        logging.info(
            f"\nDone! Downloaded: {self.stats['downloaded']}, "
            f"Failed: {self.stats['failed']}, Skipped: {self.stats['skipped']}"
        )

    def _process_journal(self, journal: dict):
        name = journal["name"]
        url = journal["url"]
        rss_url = journal.get("rss_url")
        parser_name = journal["parser"]

        logging.info(f"\n{'='*60}")
        logging.info(f"Processing: {name}")

        articles = []
        use_rss = False
        selenium_failed = False

        # Try Selenium first (more reliable — sees exactly what's on the website)
        parser = PARSERS.get(parser_name)
        if not parser:
            logging.error(f"Unknown parser '{parser_name}' for {name}")
            return

        logging.info(f"Fetching via Selenium: {url}")
        try:
            self._ensure_browser()
            soup = self.browser.get_page(url)
            self.session = self.browser.transfer_cookies_to_session()
            all_articles = parser.get_articles(soup, url)
            articles = parser.filter_by_date(all_articles, self.target_date)
            logging.info(f"Selenium: {len(all_articles)} total, {len(articles)} from {self.target_date}")
            # Treat 0 articles found as a likely Cloudflare block
            if not all_articles:
                selenium_failed = True
        except Exception as e:
            logging.warning(f"Selenium failed for {name}: {e}")
            selenium_failed = True

        # Fall back to RSS if Selenium failed (e.g., Cloudflare blocked, 0 articles)
        if selenium_failed and rss_url:
            logging.info(f"Falling back to RSS: {rss_url}")
            all_articles = self.rss_parser.fetch_articles(rss_url, parser_name)
            if all_articles:
                articles = self.rss_parser.filter_by_date(all_articles, self.target_date)
                logging.info(f"RSS: {len(all_articles)} total, {len(articles)} from {self.target_date}")
                use_rss = True

        if not articles:
            logging.info(f"No articles for {self.target_date} from {name}")
            if not self.dry_run:
                if selenium_failed:
                    suffix = "_unable_to_check"
                else:
                    suffix = "_no_paper_published"
                empty_dir = self.output_dir / f"{name}{suffix}"
                empty_dir.mkdir(parents=True, exist_ok=True)
            return

        # Create journal subfolder within the date directory
        journal_dir = self.output_dir / name
        if not self.dry_run:
            journal_dir.mkdir(parents=True, exist_ok=True)

        failed_articles = []  # Track failures for this journal

        for article in articles:
            if self.dry_run:
                logging.info(f"  [DRY RUN] {article['title']}")
                logging.info(f"            {article['url']}")
                logging.info(f"            Date: {article['date']}")
                self.stats["skipped"] += 1
            else:
                pdf_url = (self.rss_parser.get_pdf_url(article["url"], parser_name)
                           if use_rss
                           else PARSERS[parser_name].get_pdf_url(
                               self.browser.driver if self.browser else None,
                               article["url"]))
                failure_reason = self._download_article_with_url(article, pdf_url, journal_dir)
                if failure_reason:
                    failed_articles.append((article, pdf_url, failure_reason))
                time.sleep(random.uniform(2, 5))

        # Write a report for any failed downloads
        if failed_articles and not self.dry_run:
            self._write_failure_report(journal_dir, name, failed_articles)

    def _download_article_with_url(self, article: dict, pdf_url: str | None,
                                    dest_dir: Path | None = None) -> str | None:
        """Download a single article PDF. Returns failure reason string, or None on success."""
        title = article["title"]

        if not pdf_url:
            logging.warning(f"Could not determine PDF URL for: {title}")
            self.stats["failed"] += 1
            return "Could not determine PDF URL"

        filename = self._sanitize_filename(title) + ".pdf"
        filepath = (dest_dir or self.output_dir) / filename

        if filepath.exists():
            logging.info(f"  Already downloaded: {filename}")
            self.stats["skipped"] += 1
            return None

        logging.info(f"  Downloading: {title}")
        logging.debug(f"  PDF URL: {pdf_url}")

        # Try plain HTTP first
        content = self._try_download_pdf(pdf_url)

        # If blocked (403/non-PDF), fall back to Selenium
        if content is None:
            logging.info(f"  Plain download blocked, trying via browser...")
            try:
                self._ensure_browser()
                self.browser.driver.get(pdf_url)
                time.sleep(10)
                self.session = self.browser.transfer_cookies_to_session()
                content = self._try_download_pdf(pdf_url)
            except Exception as e:
                logging.error(f"  Browser fallback failed for '{title}': {e}")
                self.stats["failed"] += 1
                return f"Browser fallback error: {e}"

        if content is None:
            self.stats["failed"] += 1
            return "PDF download blocked (403/Cloudflare or invalid PDF response)"

        filepath.write_bytes(content)
        logging.info(f"  Saved: {filepath.name} ({len(content) // 1024} KB)")
        self.stats["downloaded"] += 1
        return None

    def _write_failure_report(self, journal_dir: Path, journal_name: str,
                                failed_articles: list[tuple[dict, str | None, str]]):
        """Write a txt file documenting papers that could not be downloaded."""
        report_path = journal_dir / "failed_downloads.txt"
        lines = [
            f"Failed Downloads — {journal_name}",
            f"Date: {self.target_date.isoformat()}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total failed: {len(failed_articles)}",
            "",
        ]

        for i, (article, pdf_url, reason) in enumerate(failed_articles, 1):
            lines.append(f"{'—' * 40}")
            lines.append(f"{i}. {article['title']}")
            lines.append(f"   Article URL: {article['url']}")
            if pdf_url:
                lines.append(f"   PDF URL:     {pdf_url}")
            lines.append(f"   Reason:      {reason}")
            lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logging.info(f"  Wrote failure report: {report_path.name} ({len(failed_articles)} papers)")

    def _try_download_pdf(self, pdf_url: str) -> bytes | None:
        """Attempt to download a PDF. Returns content bytes or None if failed."""
        try:
            resp = self.session.get(pdf_url, timeout=60, allow_redirects=True)
            if resp.status_code == 403:
                return None
            resp.raise_for_status()
            content = resp.content
            if content[:5] != b"%PDF-":
                logging.debug(f"  Response is not a PDF (got {content[:20]})")
                return None
            if len(content) < 10_000:
                logging.debug(f"  PDF too small ({len(content)} bytes)")
                return None
            return content
        except Exception as e:
            logging.debug(f"  Download attempt failed: {e}")
            return None

    @staticmethod
    def _sanitize_filename(title: str) -> str:
        """Convert title to a safe filename."""
        # Remove/replace unsafe characters
        safe = re.sub(r'[<>:"/\\|?*]', '', title)
        safe = re.sub(r'\s+', ' ', safe).strip()
        # Truncate to reasonable length
        if len(safe) > 150:
            safe = safe[:150].rsplit(' ', 1)[0]
        return safe


# ---------------------------------------------------------------------------
# Scheduling (launchd)
# ---------------------------------------------------------------------------
def install_schedule():
    """Install a launchd plist for daily auto-run at 12 PM."""
    python_path = sys.executable
    script_path = str(Path(__file__).resolve())

    plist = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [python_path, script_path],
        "StartCalendarInterval": {"Hour": 12, "Minute": 0},
        "StandardOutPath": str(TMP_DIR / "launchd_stdout.log"),
        "StandardErrorPath": str(TMP_DIR / "launchd_stderr.log"),
        "WorkingDirectory": str(PROJECT_DIR),
    }

    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    with open(LAUNCHD_PLIST, "wb") as f:
        plistlib.dump(plist, f)

    subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)],
                    capture_output=True)  # unload if exists
    subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST)], check=True)

    logging.info(f"Schedule installed: daily at 12:00 PM")
    logging.info(f"Plist: {LAUNCHD_PLIST}")


def uninstall_schedule():
    """Remove the launchd plist."""
    if LAUNCHD_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)],
                        capture_output=True)
        LAUNCHD_PLIST.unlink()
        logging.info("Schedule removed")
    else:
        logging.info("No schedule found to remove")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Download research papers from journal websites"
    )
    parser.add_argument("--url", help="Download from a specific journal URL only")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), default: today")
    parser.add_argument("--dry-run", action="store_true",
                        help="List papers without downloading")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging")
    parser.add_argument("--visible", action="store_true",
                        help="Show browser window (helps bypass Cloudflare)")
    parser.add_argument("--install-schedule", action="store_true",
                        help="Install daily auto-run at 12 PM")
    parser.add_argument("--uninstall-schedule", action="store_true",
                        help="Remove daily auto-run")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.install_schedule:
        install_schedule()
        return

    if args.uninstall_schedule:
        uninstall_schedule()
        return

    target_date = date.today()
    if args.date:
        target_date = date.fromisoformat(args.date)

    config = load_config()
    headless = not args.visible
    downloader = PaperDownloader(config, target_date, dry_run=args.dry_run,
                                 headless=headless)
    downloader.run(filter_url=args.url)


if __name__ == "__main__":
    main()
