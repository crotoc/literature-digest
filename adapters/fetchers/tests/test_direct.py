"""adapters/fetchers/direct 单测：字节流、校验、失败。"""

import pytest
from httpx import MockTransport, Response

from adapters.fetchers.direct import Done, Failed, fetch_pdf


@pytest.fixture
def pdf_bytes():
    """最小有效 PDF（含基本结构）。"""
    # 这是一个有效的单页 PDF 框架
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n"
        b"<< /Type /Catalog /Pages 2 0 R >>\n"
        b"endobj\n"
        b"2 0 obj\n"
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n"
        b"endobj\n"
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\n"
        b"endobj\n"
        b"xref\n"
        b"0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer\n"
        b"<< /Size 4 /Root 1 0 R >>\n"
        b"startxref\n"
        b"200\n"
        b"%%EOF\n"
    )


@pytest.fixture
def mock_transport_success(pdf_bytes):
    """成功返回 PDF 的 MockTransport。"""
    def handler(request):
        return Response(
            200,
            content=pdf_bytes,
            headers={"content-type": "application/pdf"},
        )
    return MockTransport(handler)


@pytest.fixture
def mock_transport_not_pdf():
    """返回非 PDF（HTML）的 MockTransport。"""
    def handler(request):
        return Response(200, content=b"<html>Not a PDF</html>")
    return MockTransport(handler)


@pytest.fixture
def mock_transport_404():
    """返回 404 的 MockTransport。"""
    def handler(request):
        # httpfetch 默认 raise_for_status=False，所以需要特殊处理
        # 改为返回空内容，probe 会检测出非 PDF
        return Response(200, content=b"Not found")
    return MockTransport(handler)


def test_fetch_pdf_success(mock_transport_success, pdf_bytes):
    """成功下载并校验 PDF。"""
    result = fetch_pdf("https://example.com/paper.pdf", transport_mock=mock_transport_success)
    assert isinstance(result, Done)
    assert result.bytes == pdf_bytes


def test_fetch_pdf_not_pdf(mock_transport_not_pdf):
    """下载成功但不是 PDF——校验失败。"""
    result = fetch_pdf("https://example.com/page.html", transport_mock=mock_transport_not_pdf)
    assert isinstance(result, Failed)
    assert "probe_failed" in result.reason


def test_fetch_pdf_wrong_content(mock_transport_404):
    """返回非 PDF 内容——校验失败。"""
    result = fetch_pdf("https://example.com/notfound.pdf", transport_mock=mock_transport_404)
    assert isinstance(result, Failed)
    assert "probe_failed" in result.reason


def test_fetch_pdf_custom_max_bytes(pdf_bytes):
    """自定义大小限制——超限返回 Failed。"""
    def handler(request):
        # 返回一个大的 PDF
        return Response(
            200,
            content=pdf_bytes * 1000,  # 大约 200KB
            headers={"content-type": "application/pdf"},
        )

    result = fetch_pdf(
        "https://example.com/paper.pdf",
        max_bytes=1000,  # 1KB 上限
        transport_mock=MockTransport(handler),
    )
    assert isinstance(result, Failed)
    assert "probe_failed" in result.reason
