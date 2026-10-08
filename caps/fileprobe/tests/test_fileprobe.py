"""caps/fileprobe 单测。不建库、不联网、不读外部样本。"""

import io

import pytest

from caps.fileprobe import (
    CorruptFile,
    FileTooLarge,
    UnsupportedMediaType,
    check_upload,
    probe_pdf,
    sniff,
)

from .conftest import (
    BMP,
    GIF,
    GZIP,
    JPEG,
    OLE,
    PNG,
    POSTSCRIPT,
    RTF,
    TIFF_BE,
    TIFF_LE,
    epub_bytes,
    minimal_pdf,
    zip_with,
)

PDF = minimal_pdf("Hello world")
BLANK_PDF = minimal_pdf(None)


class TestSniffMagic:
    @pytest.mark.parametrize(
        ("data", "media_type", "kind"),
        [
            (PDF, "application/pdf", "pdf"),
            (PNG, "image/png", "image"),
            (JPEG, "image/jpeg", "image"),
            (GIF, "image/gif", "image"),
            (TIFF_LE, "image/tiff", "image"),
            (TIFF_BE, "image/tiff", "image"),
            (BMP, "image/bmp", "image"),
            (GZIP, "application/gzip", "archive"),
            (RTF, "application/rtf", "text"),
            (POSTSCRIPT, "application/postscript", "document"),
            (OLE, "application/x-ole-storage", "document"),
        ],
    )
    def test_detects(self, data, media_type, kind):
        probe = sniff(data)
        assert probe.media_type == media_type
        assert probe.kind == kind

    def test_size_reported(self):
        assert sniff(PNG).size == len(PNG)

    def test_extension_is_canonical(self):
        assert sniff(PDF).extension == ".pdf"
        assert sniff(JPEG).extension == ".jpg"


class TestSniffZipFamily:
    def test_generic_zip(self):
        probe = sniff(zip_with({"a.txt": b"hi"}))
        assert probe.media_type == "application/zip"
        assert probe.kind == "archive"

    def test_epub(self):
        probe = sniff(epub_bytes())
        assert probe.media_type == "application/epub+zip"
        assert probe.extension == ".epub"
        assert probe.kind == "document"

    def test_docx(self):
        probe = sniff(zip_with({"word/document.xml": b"<w/>", "[Content_Types].xml": b"<t/>"}))
        assert probe.extension == ".docx"
        assert probe.kind == "document"

    def test_xlsx(self):
        assert sniff(zip_with({"xl/workbook.xml": b"<w/>"})).extension == ".xlsx"

    def test_pptx(self):
        assert sniff(zip_with({"ppt/presentation.xml": b"<p/>"})).extension == ".pptx"

    def test_truncated_zip_degrades_to_generic(self):
        probe = sniff(zip_with({"word/document.xml": b"<w/>"})[:40])
        assert probe.media_type == "application/zip"


class TestSniffText:
    def test_ascii(self):
        probe = sniff(b"TY  - JOUR\nTI  - A title\nER  - \n")
        assert probe.media_type == "text/plain"
        assert probe.kind == "text"

    def test_utf8_cjk(self):
        assert sniff("深度学习".encode()).media_type == "text/plain"

    def test_utf8_bom(self):
        assert sniff(b"\xef\xbb\xbfhello").media_type == "text/plain"

    def test_utf16_bom(self):
        assert sniff("hello".encode("utf-16")).media_type == "text/plain"

    def test_truncated_multibyte_still_text(self):
        # 采样边界切断 UTF-8 多字节序列，不应该被判成二进制
        payload = "深" * 2000
        assert sniff(payload.encode()).media_type == "text/plain"


class TestSniffUnknown:
    def test_random_binary(self):
        probe = sniff(b"\x00\x01\x02\x03" * 20)
        assert probe.media_type == "application/octet-stream"
        assert probe.extension == ""
        assert probe.kind == "unknown"

    def test_empty_file(self):
        probe = sniff(b"")
        assert probe.media_type == "application/octet-stream"
        assert probe.size == 0

    def test_does_not_guess_from_filename(self, tmp_path):
        """扩展名说是 .txt，内容是 PDF —— 必须按内容判。"""
        path = tmp_path / "actually_a_pdf.txt"
        path.write_bytes(PDF)
        assert sniff(path).media_type == "application/pdf"

    def test_does_not_trust_extension_the_other_way(self, tmp_path):
        path = tmp_path / "fake.pdf"
        path.write_bytes(b"\x00\x01\x02\x03" * 20)
        assert sniff(path).media_type == "application/octet-stream"


class TestInputForms:
    def test_bytes_path_and_stream_agree(self, tmp_path):
        path = tmp_path / "a.pdf"
        path.write_bytes(PDF)
        from_bytes = sniff(PDF)
        from_path = sniff(path)
        from_str = sniff(str(path))
        with path.open("rb") as handle:
            from_stream = sniff(handle)
        assert from_bytes == from_path == from_str == from_stream

    def test_stream_position_restored(self):
        stream = io.BytesIO(PDF)
        stream.seek(7)
        sniff(stream, deep=True)
        # 本模块会移动游标，但不应该关闭调用方的流
        assert not stream.closed

    def test_caller_stream_not_closed(self):
        stream = io.BytesIO(PNG)
        sniff(stream)
        assert not stream.closed

    def test_bytearray_accepted(self):
        assert sniff(bytearray(PNG)).media_type == "image/png"

    def test_non_seekable_stream_rejected(self):
        class OneWay(io.RawIOBase):
            def read(self, size=-1):
                return b""

            def seek(self, *args):
                raise OSError("nope")

            def seekable(self):
                return False

        with pytest.raises(ValueError, match="不可 seek"):
            sniff(OneWay())

    def test_wrong_type_rejected(self):
        with pytest.raises(TypeError):
            sniff(12345)


class TestDeepPdf:
    def test_shallow_leaves_pdf_none(self):
        assert sniff(PDF).pdf is None

    def test_deep_fills_pdf(self):
        info = sniff(PDF, deep=True).pdf
        assert info is not None
        assert info.page_count == 1
        assert info.version == "1.4"
        assert info.encrypted is False
        assert info.damaged is False

    def test_deep_on_non_pdf_is_noop(self):
        assert sniff(PNG, deep=True).pdf is None

    def test_text_layer_detected(self):
        assert sniff(PDF, deep=True).pdf.has_text_layer is True

    def test_blank_page_has_no_text_layer(self):
        assert sniff(BLANK_PDF, deep=True).pdf.has_text_layer is False

    def test_version_from_header(self):
        info = sniff(minimal_pdf("x", version="1.7"), deep=True).pdf
        assert info.version == "1.7"


class TestProbePdf:
    def test_returns_none_for_non_pdf(self):
        assert probe_pdf(PNG) is None
        assert probe_pdf(b"") is None

    def test_reads_page_count(self):
        assert probe_pdf(PDF).page_count == 1

    def test_truncated_pdf_is_damaged(self):
        info = probe_pdf(PDF[: len(PDF) // 3])
        assert info is not None
        assert info.damaged is True
        assert info.page_count is None

    def test_header_only_is_damaged(self):
        info = probe_pdf(b"%PDF-1.4\n")
        assert info is not None
        assert info.damaged is True

    def test_accepts_path(self, tmp_path):
        path = tmp_path / "a.pdf"
        path.write_bytes(PDF)
        assert probe_pdf(path).page_count == 1


class TestCheckUpload:
    def test_passes(self):
        probe = check_upload(PDF, max_bytes=10_000_000)
        assert probe.media_type == "application/pdf"

    def test_too_large(self):
        with pytest.raises(FileTooLarge) as caught:
            check_upload(PDF, max_bytes=10)
        assert caught.value.max_bytes == 10
        assert caught.value.size == len(PDF)

    def test_exact_limit_passes(self):
        assert check_upload(PDF, max_bytes=len(PDF)).size == len(PDF)

    def test_one_byte_over_fails(self):
        with pytest.raises(FileTooLarge):
            check_upload(PDF, max_bytes=len(PDF) - 1)

    def test_media_type_whitelist(self):
        with pytest.raises(UnsupportedMediaType) as caught:
            check_upload(PNG, max_bytes=10_000, allowed_media_types={"application/pdf"})
        assert caught.value.media_type == "image/png"

    def test_whitelist_allows_member(self):
        allowed = {"application/pdf", "image/png"}
        assert check_upload(PNG, max_bytes=10_000, allowed_media_types=allowed).kind == "image"

    def test_none_whitelist_allows_anything(self):
        assert check_upload(b"\x00\x01" * 20, max_bytes=10_000).kind == "unknown"

    def test_reject_damaged(self):
        with pytest.raises(CorruptFile):
            check_upload(PDF[: len(PDF) // 3], max_bytes=10_000, reject_damaged=True)

    def test_damaged_tolerated_by_default(self):
        assert check_upload(PDF[: len(PDF) // 3], max_bytes=10_000).media_type == "application/pdf"

    def test_size_checked_before_type(self):
        """大小是最便宜的判据，应该先拦——否则大文件白白解析一遍。"""
        with pytest.raises(FileTooLarge):
            check_upload(PDF, max_bytes=1, allowed_media_types={"image/png"})

    def test_nonpositive_max_bytes_rejected(self):
        with pytest.raises(ValueError):
            check_upload(PDF, max_bytes=0)
