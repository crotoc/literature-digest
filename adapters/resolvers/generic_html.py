"""通用 HTML 层 resolver：从页面 meta 标签和 DOM 元素推导 PDF 候选。

不登录、不读 cookie、不下载——只从 HTML 解析推导候选 URL。
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from caps.httpfetch import fetch


@dataclass
class Candidate:
    url: str
    kind: str  # "direct_pdf", "article_page", "metadata"
    source: str  # "citation_pdf_url", "og:pdf", "pdf_link_text"
    priority: int  # 1=highest


def resolve(article_url: str, transport_mock=None) -> list[Candidate]:
    """
    尝试从文章 URL 提取 PDF 候选链接。
    
    策略（优先级从高到低）：
    1. <meta name="citation_pdf_url" content="..." />
    2. <meta name="og:pdf" content="..." />
    3. <link rel="alternate" type="application/pdf" href="..." />
    4. 页面中所有 href 匹配 /pdf|/fulltext\.pdf 的 <a> 标签
    
    返回去重后的候选列表，按 priority 排序。
    """
    candidates = []
    
    try:
        # 使用 caps/httpfetch 抓页面（带 UA、SSRF 防护、重试等）
        if transport_mock:
            # 测试用 mock
            result = fetch(article_url, transport=transport_mock)
        else:
            result = fetch(article_url)
        
        html = result.content.decode("utf-8", errors="ignore")
        
        # 解析 meta 标签和 link 元素
        parser = _PDFMetaParser()
        parser.feed(html)
        
        # citation_pdf_url（最高优先级）
        if parser.citation_pdf_url:
            candidates.append(Candidate(
                url=_resolve_url(parser.citation_pdf_url, article_url),
                kind="direct_pdf",
                source="citation_pdf_url",
                priority=1,
            ))
        
        # og:pdf
        if parser.og_pdf:
            candidates.append(Candidate(
                url=_resolve_url(parser.og_pdf, article_url),
                kind="direct_pdf",
                source="og:pdf",
                priority=2,
            ))
        
        # rel="alternate" type="application/pdf"
        if parser.pdf_link_rel:
            candidates.append(Candidate(
                url=_resolve_url(parser.pdf_link_rel, article_url),
                kind="direct_pdf",
                source="rel_alternate_pdf",
                priority=3,
            ))
        
        # <a> 标签匹配 /pdf
        for link in parser.pdf_like_hrefs:
            candidates.append(Candidate(
                url=_resolve_url(link, article_url),
                kind="article_page",
                source="pdf_link_text",
                priority=4,
            ))
    
    except Exception:
        # 网络错误或解析失败时返回空列表
        # features/fulltext 会在 reason 里记录错误
        pass
    
    # 去重（按 URL）
    seen = set()
    unique = []
    for c in candidates:
        if c.url not in seen:
            seen.add(c.url)
            unique.append(c)
    
    # 按优先级排序
    return sorted(unique, key=lambda c: c.priority)


class _PDFMetaParser(HTMLParser):
    """提取 meta 和 link 标签中的 PDF 信息。"""
    
    def __init__(self):
        super().__init__()
        self.citation_pdf_url = None
        self.og_pdf = None
        self.pdf_link_rel = None
        self.pdf_like_hrefs = []
    
    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        if tag == "meta":
            name = attrs_dict.get("name", "").lower()
            property_ = attrs_dict.get("property", "").lower()
            content = attrs_dict.get("content", "")
            
            if name == "citation_pdf_url" and content:
                self.citation_pdf_url = content
            elif property_ == "og:pdf" and content:
                self.og_pdf = content
        
        elif tag == "link":
            rel = attrs_dict.get("rel", "").lower()
            type_ = attrs_dict.get("type", "").lower()
            href = attrs_dict.get("href", "")
            
            if "alternate" in rel and "application/pdf" in type_ and href:
                self.pdf_link_rel = href
        
        elif tag == "a":
            href = attrs_dict.get("href", "")
            if href and re.search(r"(\.pdf|/pdf|fulltext)", href, re.IGNORECASE):
                self.pdf_like_hrefs.append(href)


def _resolve_url(relative_or_absolute: str, base_url: str) -> str:
    """相对 URL 转绝对 URL（相对于 base_url）。"""
    from urllib.parse import urljoin
    return urljoin(base_url, relative_or_absolute)
