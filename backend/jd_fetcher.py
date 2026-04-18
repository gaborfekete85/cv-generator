"""Try to fetch a job description from a URL.

LinkedIn aggressively blocks unauthenticated fetches, so this is best-effort.
If it fails, the frontend should prompt the user to paste the text instead.

Text extraction uses ``separator=" "`` so that inline tags don't concatenate
their text content into garbage like "ismsUnderstanding" — a problem we saw
with the previous implementation.
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _extract_text(element) -> str:
    """Pull clean text out of a BeautifulSoup element.

    We insert newlines at block-level boundaries before calling get_text so
    lists and paragraphs don't glue onto the preceding sentence. Inline
    elements are separated with a space.
    """
    if element is None:
        return ""
    # Replace <br> with newlines so "Line1<br>Line2" doesn't become "Line1Line2".
    for br in element.find_all("br"):
        br.replace_with("\n")
    # Insert a newline before block-level tags so list items and paragraphs
    # don't run together.
    block_tags = ("p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4",
                  "h5", "h6", "section", "article", "tr")
    for tag in element.find_all(block_tags):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = element.get_text(separator=" ", strip=True)
    # Collapse runs of whitespace, preserve paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_job_description(url: str, timeout: float = 10.0) -> tuple[str, str | None]:
    """Return (text, detected_title).

    Raises httpx.HTTPError on network failure; raises ValueError if the page
    looks like an auth wall.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
    }
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")

    # Nuke obvious noise.
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "noscript", "aside", "form", "iframe", "svg"]):
        tag.decompose()

    title = None
    if soup.title and soup.title.text:
        title = soup.title.text.strip()

    # Heuristics: look for the most specific container first, then widen out.
    candidates = [
        # Common LinkedIn / ATS selectors
        {"attrs": {"class": re.compile(r"(show-more-less-html|description__text|jobs-description|job-description|job_description)", re.I)}},
        {"attrs": {"class": re.compile(r"(description|jobs-description|posting|job-details)", re.I)}},
        {"attrs": {"id": re.compile(r"(description|job)", re.I)}},
        {"name": "article", "attrs": {}},
        {"name": "main", "attrs": {}},
    ]
    best_text = ""
    for c in candidates:
        for el in soup.find_all(c.get("name"), attrs=c.get("attrs", {})):
            txt = _extract_text(el)
            if len(txt) > len(best_text):
                best_text = txt

    if not best_text or len(best_text) < 200:
        best_text = _extract_text(soup.body) if soup.body else html

    # Detect auth walls (LinkedIn, etc.)
    lowered = best_text.lower()
    blockers = ("sign in to view", "join linkedin", "authwall",
                "log in to continue", "please sign in", "you must be logged in")
    if any(b in lowered for b in blockers) and len(best_text) < 3000:
        raise ValueError(
            "This page requires sign-in (likely LinkedIn). Paste the job description "
            "text directly instead."
        )

    return best_text, title
