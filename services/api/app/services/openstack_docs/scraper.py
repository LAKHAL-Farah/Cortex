"""Fetches and chunks official OpenStack documentation pages (v1.0, see
docs/architecture/adr-0010-openstack-expert-v1-expansion.md).

Split into two pure/testable pieces, same separation ../knowledge/loader.py
already established for the internal-docs pipeline:

- `chunk_html` never touches the network -- it takes raw HTML text and
  returns `DocChunk`s, so it's trivially unit-testable against a literal
  HTML string (see tests/test_openstack_docs_scraper.py), the same way
  loader.chunk_markdown is tested against a literal Markdown string.
- `fetch_page`/`fetch_and_chunk` do the actual HTTP GET and are what
  ingest.py calls; a network/HTTP failure raises `ScrapeError` rather than
  propagating requests' own exception types, so callers only need to know
  about one error type for "this page didn't come back" (same shape as
  ../knowledge/embeddings.py's `EmbeddingError`).

Chunking strategy: docs.openstack.org is Sphinx-generated, so real content
always sits inside one particular container (`_MAIN_CONTENT_SELECTORS`
tries them in order, matching everything the current and recent Sphinx
themes have used) with a nav/sidebar/footer around it that would otherwise
pollute every chunk with the same boilerplate. Within that container,
content is split on heading boundaries (h1-h4) as one chunk per section --
conceptually the same heading-based split loader.py uses for Markdown, just
applied to the DOM: a unique marker string is inserted immediately before
every heading tag, then the whole container's text is serialized once with
`get_text()` and split back apart on that marker. This (rather than walking
`.descendants` and manually collecting `<p>`/`<li>`/... tags) is what avoids
double-counting text in nested markup (e.g. a `<p>` inside a `<li>`) --
`get_text()` already walks the tree exactly once in document order, so
splitting its *output* on inserted markers can't double-count anything the
walk itself wouldn't have.
"""
import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Distinct from loader.py's own _CHUNK_ID_NAMESPACE -- these are different
# corpora (source_url vs source_path as the identity key), so there's no
# reason their UUID5 chunk ids should ever collide, and keeping the
# namespaces apart makes that a structural guarantee rather than a
# coincidence of the input strings never matching.
_CHUNK_ID_NAMESPACE = uuid.UUID("2f6f1c3a-8b2b-4e36-9c0a-6b6a9f3e6a10")

# Same rationale as loader.MAX_CHUNK_CHARS: keeps a single embedding
# call/vector from having to represent an oversized section (a long
# command reference or table-heavy admin-guide page).
MAX_CHUNK_CHARS = 2000

REQUEST_TIMEOUT_SECONDS = float(os.environ.get("OPENSTACK_DOCS_FETCH_TIMEOUT", "15"))
_USER_AGENT = "Cortex-OpenStack-Expert-Agent/1.0 (+internal docs ingest)"

# Tried in order -- covers the Sphinx themes docs.openstack.org has used
# (the classic "openstackdocs" theme's `div[role=main]`, and the
# more the modern container div.) `body` is the final fallback so a page
# using a theme this list doesn't recognize still yields *something*
# rather than zero chunks.
_MAIN_CONTENT_SELECTORS = ['div[role="main"]', "main", "div.document", "div.body"]

_STRIP_TAGS = ["script", "style", "nav", "header", "footer", "aside", "noscript"]

# Inserted as a literal NavigableString immediately before every heading
# tag so a single main.get_text() pass can be split back into per-heading
# sections afterward -- see module docstring for why this beats walking
# .descendants directly. Deliberately not something that could plausibly
# appear in real documentation prose.
_HEADING_MARKER = "\x00CORTEX_HEADING\x00"


class ScrapeError(RuntimeError):
    """A page couldn't be fetched -- network error, timeout, or non-2xx.
    Callers (ingest.py) catch this per-URL and continue with the rest of
    the source list rather than failing the whole ingest run; see that
    module's docstring."""


@dataclass
class DocChunk:
    id: str
    text: str
    source_url: str
    doc_title: str
    heading: str | None
    service: str
    chunk_index: int


def _chunk_id(source_url: str, chunk_index: int) -> str:
    digest = hashlib.sha1(f"{source_url}::{chunk_index}".encode("utf-8")).hexdigest()
    return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, digest))


def _split_oversized(text: str, max_chars: int) -> list[str]:
    """Same paragraph-boundary splitter as loader._split_oversized --
    kept as its own small copy here rather than imported cross-module so
    this package stays independently testable/movable without reaching
    into ../knowledge/'s internals for a ~15-line algorithm. See that
    module's own comment for the reasoning; identical behavior here."""
    if len(text) <= max_chars:
        return [text]
    parts, buf = [], []
    length = 0
    for para in text.split("\n\n"):
        if length + len(para) > max_chars and buf:
            parts.append("\n\n".join(buf))
            buf, length = [], 0
        buf.append(para)
        length += len(para) + 2
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def _extract_main_content(soup: BeautifulSoup):
    for selector in _MAIN_CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            return node
    return soup.body or soup


def _page_title(soup: BeautifulSoup, fallback: str) -> str:
    h1 = soup.find("h1")
    if h1 is not None:
        text = h1.get_text(" ", strip=True)
        if text:
            return text
    if soup.title is not None:
        text = soup.title.get_text(strip=True)
        if text:
            return text
    return fallback


def chunk_html(html: str, source_url: str, service: str) -> list[DocChunk]:
    """Pure -- no network. See module docstring for the marker-based
    heading-split strategy."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()

    main = _extract_main_content(soup)
    title = _page_title(soup, fallback=source_url)

    headings = main.find_all(["h1", "h2", "h3", "h4"])
    for heading in headings:
        heading.insert_before(_HEADING_MARKER)

    full_text = main.get_text("\n", strip=True)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)

    raw_sections = full_text.split(_HEADING_MARKER)
    sections: list[tuple[str | None, str]] = []

    preamble = raw_sections[0].strip()
    if preamble:
        sections.append((None, preamble))

    for raw in raw_sections[1:]:
        raw = raw.strip()
        if not raw:
            continue
        heading_line, _, body = raw.partition("\n")
        heading_text = heading_line.strip()
        body = body.strip()
        section_text = f"{heading_text}\n{body}" if body else heading_text
        sections.append((heading_text or None, section_text))

    chunks: list[DocChunk] = []
    idx = 0
    for heading, text in sections:
        for piece in _split_oversized(text, MAX_CHUNK_CHARS):
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(
                DocChunk(
                    id=_chunk_id(source_url, idx),
                    text=piece,
                    source_url=source_url,
                    doc_title=title,
                    heading=heading,
                    service=service,
                    chunk_index=idx,
                )
            )
            idx += 1
    return chunks


def fetch_page(url: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ScrapeError(f"failed to fetch {url}: {exc}") from exc
    # requests falls back to ISO-8859-1 for text/* responses with no charset
    # header, which turns every UTF-8 quote/pilcrow into mojibake ("Â¶",
    # "â\x80\x9c") in the stored chunks. docs.openstack.org is UTF-8.
    headers = getattr(response, "headers", None) or {}
    if "charset" not in str(headers.get("content-type", "")).lower():
        response.encoding = "utf-8"
    return response.text


def fetch_and_chunk(url: str, service: str) -> list[DocChunk]:
    html = fetch_page(url)
    return chunk_html(html, url, service)
