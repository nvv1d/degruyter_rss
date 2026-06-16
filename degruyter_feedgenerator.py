#!/usr/bin/env python3
# coding: utf-8
"""
De Gruyter RSS Feed Generator
Adapted from https://github.com/alexander-winkler/degruyter_rss
Generates Atom feeds for selected Linguistics/Semiotics/ELT/Applied Linguistics journals
and commits them to the repository so they are accessible via raw.githubusercontent.com.
"""

import csv
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from lxml import etree

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_OWNER = os.environ.get("GITHUB_REPOSITORY_OWNER", "nvv1d")
REPO_NAME_RAW = os.environ.get("GITHUB_REPOSITORY", "nvv1d/AcademicRSSFeed-TG")
REPO_NAME = REPO_NAME_RAW.split("/")[-1] if "/" in REPO_NAME_RAW else REPO_NAME_RAW
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")

FEED_DIR = Path(__file__).parent / "feed"
JOURNALS_CSV = Path(__file__).parent / "journals.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DeGruyterRSSBot/1.0; "
        "+https://github.com/nvv1d/AcademicRSSFeed-TG)"
    )
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_uuid() -> str:
    return str(uuid.uuid4())


def raw_feed_url(key: str) -> str:
    """Return the externally reachable URL for a generated feed.

    raw.githubusercontent.com returns 404 for private repositories unless the
    request is authenticated, so set DEGRUYTER_FEED_BASE_URL to a public mirror
    or Vercel/static URL when this repository is private.
    """
    public_base = os.environ.get("DEGRUYTER_FEED_BASE_URL", "").rstrip("/")
    if public_base:
        return f"{public_base}/{key}.xml"
    return (
        f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}"
        f"/{BRANCH}/degruyter/feed/{key}.xml"
    )


def get_latest_issue(key: str):
    """Return (journal_title, latest_issue_url) for a De Gruyter journal key."""
    url = f"https://www.degruyterbrill.com/journal/key/{key}/html"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    parsed = urlparse(url)
    soup = BeautifulSoup(resp.content, "html.parser")

    title = soup.title.string if soup.title else "title not available"

    link_tag = soup.find("a", id="view-latest-issue")
    if not link_tag or not link_tag.get("href"):
        raise ValueError(f"Could not find latest-issue link for key={key}")

    issue_url = f"{parsed.scheme}://{parsed.hostname}{link_tag['href']}"
    return title, issue_url


def parse_issue_page(url: str):
    """Return (issue_title, list_of_article_dicts) from an issue page."""
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    parsed = urlparse(url)
    soup = BeautifulSoup(resp.content, "html.parser")

    issue_title = soup.title.string if soup.title else "issue title not available"
    items = []

    for li in soup.select("ul.issue-content-list li"):
        a_tag = li.find("a", class_="text-dark", attrs={"data-doi": True, "href": True})
        title_span = li.find("span", class_="text-dark ahead-of-print-title")
        details_div = li.find("div", class_="ahead-of-print-details")

        if a_tag and details_div:
            items.append(
                {
                    "doi": a_tag.get("data-doi"),
                    "href": f"{parsed.scheme}://{parsed.hostname}{a_tag.get('href')}",
                    "title": title_span.get_text(strip=True) if title_span else None,
                    "date": (
                        details_div.find("div", class_="date").get_text(strip=True)
                        if details_div.find("div", class_="date")
                        else None
                    ),
                    "authors": (
                        details_div.find("div", class_="authors").get_text(strip=True)
                        if details_div.find("div", class_="authors")
                        else None
                    ),
                    "page_range": (
                        details_div.find("span", class_="pageRange").get_text(strip=True)
                        if details_div.find("span", class_="pageRange")
                        else None
                    ),
                }
            )

    return issue_title, items


def is_local_feed_older(key: str, latest_issue_url: str) -> bool:
    """Return True if the local XML feed does not already point at latest_issue_url."""
    feed_path = FEED_DIR / f"{key}.xml"
    if not feed_path.exists():
        return True

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        tree = etree.parse(str(feed_path))
        stored = tree.xpath('./atom:link[@rel="via"]/@href', namespaces=ns)
        if stored and stored[0].strip() == latest_issue_url.strip():
            return False
    except Exception:
        pass  # malformed XML → regenerate

    return True


def generate_feed(key: str, journal_title: str, journal_url: str, issue_items: list):
    """Write an Atom XML feed to degruyter/feed/<key>.xml."""
    FEED_DIR.mkdir(parents=True, exist_ok=True)

    nsmap = {None: "http://www.w3.org/2005/Atom"}
    root = etree.Element("feed", nsmap=nsmap)

    # Channel metadata
    etree.SubElement(root, "title").text = journal_title
    etree.SubElement(root, "link", href=journal_url, rel="via")
    etree.SubElement(
        root,
        "link",
        href=raw_feed_url(key),
        type="application/atom+xml",
        rel="self",
    )
    etree.SubElement(
        root,
        "link",
        href=f"https://www.degruyterbrill.com/journal/key/{key}/html",
        rel="related",
    )
    etree.SubElement(root, "updated").text = create_timestamp()
    etree.SubElement(root, "id").text = raw_feed_url(key)
    etree.SubElement(
        root, "generator"
    ).text = "https://github.com/nvv1d/AcademicRSSFeed-TG/blob/main/degruyter/degruyter_feedgenerator.py"
    etree.SubElement(
        root, "subtitle"
    ).text = f"Latest articles from {journal_title} (De Gruyter)"

    # Entries
    for item in issue_items:
        entry = etree.SubElement(root, "entry")
        etree.SubElement(entry, "title").text = item.get("title") or "(no title)"
        etree.SubElement(entry, "link", href=item.get("href", ""))
        doi = item.get("doi", "")
        etree.SubElement(entry, "id").text = f"https://doi.org/{doi}"
        etree.SubElement(entry, "updated").text = create_timestamp()

        summary_parts = []
        if item.get("authors"):
            summary_parts.append(f"Authors: {item['authors']}")
        if doi:
            summary_parts.append(f"DOI: https://doi.org/{doi}")
        if item.get("page_range"):
            summary_parts.append(f"Pages: {item['page_range']}")
        etree.SubElement(entry, "summary").text = "\n".join(summary_parts)

    tree = etree.ElementTree(root)
    out_path = FEED_DIR / f"{key}.xml"
    tree.write(
        str(out_path),
        pretty_print=True,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
    print(f"  ✓  {key}: feed written → {out_path}")


def process_journal(key: str, title_hint: str = "") -> bool:
    """
    Full pipeline for one journal key.
    Returns True if the feed was updated, False if already current.
    """
    try:
        journal_title, latest_url = get_latest_issue(key)
    except Exception as exc:
        print(f"  ✗  {key}: could not fetch journal page — {exc}")
        return False

    if not is_local_feed_older(key, latest_url):
        print(f"  –  {key} ({journal_title}): already up to date")
        return False

    try:
        _, items = parse_issue_page(latest_url)
    except Exception as exc:
        print(f"  ✗  {key}: could not parse issue page — {exc}")
        return False

    generate_feed(key, journal_title, latest_url, items)
    return True


def load_journals(csv_path: Path) -> list[dict]:
    """Load journals CSV and normalise column names to canonical keys."""
    # The original De Gruyter feed_list.csv uses these headers:
    #   Journal Code Klopotek, Journal Code Online, Title,
    #   Print-ISSN, Online-ISSN, Subject Area, URL, rss_feed
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    normalised = []
    for row in rows:
        # Accept both the original header names and any pre-normalised ones
        key = (
            row.get("Journal Code Online")
            or row.get("key")
            or ""
        ).strip()
        title = (
            row.get("Title")
            or row.get("title")
            or ""
        ).strip()
        subject = (
            row.get("Subject Area")
            or row.get("subject")
            or ""
        ).strip()
        if key:
            normalised.append({"key": key, "title": title, "subject": subject})

    return normalised


def main():
    journals = load_journals(JOURNALS_CSV)
    print(f"Processing {len(journals)} journals …\n")

    updated = 0
    failed = []

    for journal in journals:
        key = journal["key"]
        title_hint = journal.get("title", "")
        print(f"→ {key}  ({title_hint})")
        try:
            changed = process_journal(key, title_hint)
            if changed:
                updated += 1
        except Exception as exc:
            print(f"  ✗  {key}: unexpected error — {exc}")
            failed.append(key)
        time.sleep(1.5)  # polite crawl delay

    print(f"\nDone. {updated} feed(s) updated, {len(failed)} failed.")
    if failed:
        print("Failed keys:", ", ".join(failed))

    # Write a summary CSV of all feeds with their raw.githubusercontent URLs
    summary_path = Path(__file__).parent / "feed_index.csv"
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["key", "title", "subject", "feed_url"])
        for j in journals:
            k = j["key"]
            writer.writerow(
                [k, j.get("title", ""), j.get("subject", ""), raw_feed_url(k)]
            )
    print(f"Feed index written → {summary_path}")


if __name__ == "__main__":
    main()
