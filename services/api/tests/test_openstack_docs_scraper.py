"""Tests for services/openstack_docs/scraper.py's `chunk_html` -- pure, no
network, same shape as tests/test_knowledge_loader.py's tests for
loader.chunk_markdown (a literal input string in, `DocChunk`s out).
`fetch_page`/`fetch_and_chunk` (the two functions that actually touch the
network) are exercised via monkeypatching `requests.get` instead of a real
HTTP call, same pattern tests/test_security_agent.py uses for cve_feed.
"""
import pytest

from app.services.openstack_docs import scraper


SAMPLE_HTML = """
<html>
<head><title>Nova Troubleshooting Guide</title></head>
<body>
<nav>skip this nav content</nav>
<div role="main">
<h1>Troubleshoot Compute</h1>
<p>Intro paragraph before any section heading.</p>
<h2>Common errors</h2>
<p>Instances stuck in ERROR usually mean the scheduler couldn't place them.</p>
<p>Check the nova-scheduler logs first.</p>
<h2>Empty log files</h2>
<p>If the log file is empty, the service likely isn't running at all.</p>
</div>
<footer>skip this footer content</footer>
<script>console.log("skip this too")</script>
</body>
</html>
"""


def test_chunk_html_strips_nav_and_footer_and_script():
    chunks = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/nova/latest/x.html", "nova")
    combined = " ".join(c.text for c in chunks)
    assert "skip this nav content" not in combined
    assert "skip this footer content" not in combined
    assert "skip this too" not in combined


def test_chunk_html_splits_on_headings():
    chunks = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/nova/latest/x.html", "nova")
    headings = [c.heading for c in chunks]
    # SAMPLE_HTML's <h1> is the very first thing inside the main content
    # container, so there's no text before it to become a preamble
    # section -- see test_chunk_html_keeps_text_before_first_heading_as_a_preamble_section
    # below for the case where there is one.
    assert "Troubleshoot Compute" in headings
    assert "Common errors" in headings
    assert "Empty log files" in headings


def test_chunk_html_keeps_text_before_first_heading_as_a_preamble_section():
    html = (
        "<html><body><div role='main'>"
        "<p>Text that appears before any heading at all.</p>"
        "<h2>First real section</h2><p>body</p>"
        "</div></body></html>"
    )
    chunks = scraper.chunk_html(html, "https://docs.openstack.org/preamble.html", "nova")
    preamble = next(c for c in chunks if c.heading is None)
    assert "Text that appears before any heading" in preamble.text


def test_chunk_html_keeps_heading_text_and_body_together():
    chunks = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/nova/latest/x.html", "nova")
    common_errors = next(c for c in chunks if c.heading == "Common errors")
    assert "Common errors" in common_errors.text
    assert "scheduler couldn't place them" in common_errors.text
    assert "nova-scheduler logs" in common_errors.text


def test_chunk_html_uses_h1_as_doc_title():
    chunks = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/nova/latest/x.html", "nova")
    assert all(c.doc_title == "Troubleshoot Compute" for c in chunks)


def test_chunk_html_falls_back_to_title_tag_with_no_h1():
    html = "<html><head><title>Fallback Title</title></head><body><p>just a paragraph</p></body></html>"
    chunks = scraper.chunk_html(html, "https://docs.openstack.org/x.html", "cinder")
    assert chunks
    assert all(c.doc_title == "Fallback Title" for c in chunks)


def test_chunk_html_sets_source_url_and_service_on_every_chunk():
    chunks = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/nova/latest/x.html", "nova")
    assert chunks
    assert all(c.source_url == "https://docs.openstack.org/nova/latest/x.html" for c in chunks)
    assert all(c.service == "nova" for c in chunks)


def test_chunk_html_assigns_sequential_chunk_index():
    chunks = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/nova/latest/x.html", "nova")
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_html_ids_are_stable_and_deterministic():
    a = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/x.html", "nova")
    b = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/x.html", "nova")
    assert [c.id for c in a] == [c.id for c in b]


def test_chunk_html_ids_differ_across_source_urls():
    a = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/a.html", "nova")
    b = scraper.chunk_html(SAMPLE_HTML, "https://docs.openstack.org/b.html", "nova")
    assert a[0].id != b[0].id


def test_chunk_html_splits_an_oversized_section():
    long_body = "\n\n".join(f"Paragraph number {i} with some real content in it." for i in range(200))
    html = f"<html><body><div role='main'><h1>T</h1><h2>Big section</h2><p>{long_body}</p></div></body></html>"
    chunks = scraper.chunk_html(html, "https://docs.openstack.org/big.html", "nova")
    big_section_chunks = [c for c in chunks if c.heading == "Big section"]
    assert len(big_section_chunks) > 1
    assert all(len(c.text) <= scraper.MAX_CHUNK_CHARS for c in big_section_chunks)


def test_chunk_html_handles_no_headings_at_all():
    html = "<html><body><div role='main'><p>Just one paragraph, no headings anywhere.</p></div></body></html>"
    chunks = scraper.chunk_html(html, "https://docs.openstack.org/plain.html", "keystone")
    assert len(chunks) == 1
    assert chunks[0].heading is None
    assert "Just one paragraph" in chunks[0].text


def test_chunk_html_empty_page_yields_no_chunks():
    html = "<html><body><div role='main'></div></body></html>"
    assert scraper.chunk_html(html, "https://docs.openstack.org/empty.html", "glance") == []


# ------------------------------------------------------------------ fetch --

class _FakeResponse:
    def __init__(self, text, status_ok=True):
        self.text = text
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise scraper.requests.HTTPError("404")


def test_fetch_page_returns_response_text(monkeypatch):
    monkeypatch.setattr(scraper.requests, "get", lambda url, timeout, headers: _FakeResponse("<html>ok</html>"))
    assert scraper.fetch_page("https://docs.openstack.org/x.html") == "<html>ok</html>"


def test_fetch_page_wraps_request_exceptions(monkeypatch):
    def fake_get(url, timeout, headers):
        raise scraper.requests.ConnectionError("dns failure")
    monkeypatch.setattr(scraper.requests, "get", fake_get)
    with pytest.raises(scraper.ScrapeError):
        scraper.fetch_page("https://docs.openstack.org/x.html")


def test_fetch_page_wraps_http_errors(monkeypatch):
    monkeypatch.setattr(
        scraper.requests, "get", lambda url, timeout, headers: _FakeResponse("", status_ok=False)
    )
    with pytest.raises(scraper.ScrapeError):
        scraper.fetch_page("https://docs.openstack.org/missing.html")


def test_fetch_and_chunk_combines_fetch_and_chunk(monkeypatch):
    monkeypatch.setattr(scraper, "fetch_page", lambda url: SAMPLE_HTML)
    chunks = scraper.fetch_and_chunk("https://docs.openstack.org/nova/latest/x.html", "nova")
    assert chunks
    assert all(c.service == "nova" for c in chunks)
