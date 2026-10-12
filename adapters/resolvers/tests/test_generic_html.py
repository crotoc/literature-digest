"""adapters/resolvers/generic_html 单测：meta 标签、link 标签、链接文本。"""

import pytest
from httpx import MockTransport, Response

from adapters.resolvers.generic_html import resolve


@pytest.fixture
def mock_transport():
    """模拟 HTTP 响应的 MockTransport。"""
    def handler(request):
        url = request.url.path
        
        if "/citation_pdf" in url:
            html = '''<html><meta name="citation_pdf_url" content="https://example.com/paper.pdf"></html>'''
        elif "/og_pdf" in url:
            html = '''<html><meta property="og:pdf" content="https://example.com/full.pdf"></html>'''
        elif "/link_rel" in url:
            html = '''<html><link rel="alternate" type="application/pdf" href="https://example.com/pdf"></html>'''
        elif "/a_tags" in url:
            html = '''<html><a href="https://example.com/download.pdf">Download</a><a href="/pdf/file.pdf">PDF</a></html>'''
        else:
            html = '<html></html>'
        
        return Response(200, content=html.encode())
    
    return MockTransport(handler)


def test_resolve_citation_pdf_url(mock_transport):
    """识别 citation_pdf_url meta 标签。"""
    candidates = resolve("https://example.com/citation_pdf", transport_mock=mock_transport)
    assert len(candidates) == 1
    assert candidates[0].url == "https://example.com/paper.pdf"
    assert candidates[0].source == "citation_pdf_url"
    assert candidates[0].priority == 1


def test_resolve_og_pdf(mock_transport):
    """识别 og:pdf meta 标签。"""
    candidates = resolve("https://example.com/og_pdf", transport_mock=mock_transport)
    assert len(candidates) == 1
    assert candidates[0].url == "https://example.com/full.pdf"
    assert candidates[0].source == "og:pdf"
    assert candidates[0].priority == 2


def test_resolve_link_rel_alternate(mock_transport):
    """识别 rel="alternate" type="application/pdf" link 标签。"""
    candidates = resolve("https://example.com/link_rel", transport_mock=mock_transport)
    assert len(candidates) == 1
    assert candidates[0].url == "https://example.com/pdf"
    assert candidates[0].source == "rel_alternate_pdf"


def test_resolve_a_tags(mock_transport):
    """识别页面中的 PDF 链接（/pdf、.pdf 文件）。"""
    candidates = resolve("https://example.com/a_tags", transport_mock=mock_transport)
    # 两个 <a> 标签都应被识别
    assert len(candidates) >= 2
    urls = {c.url for c in candidates}
    assert "https://example.com/download.pdf" in urls
    assert "https://example.com/pdf/file.pdf" in urls


def test_resolve_empty_page(mock_transport):
    """空 HTML 页面返回空候选列表。"""
    candidates = resolve("https://example.com/notfound", transport_mock=mock_transport)
    assert candidates == []
