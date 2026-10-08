"""caps/archive 单测。不碰磁盘，全部在内存 BytesIO 里拼。"""

import io
import zipfile

import pytest

from caps.archive import (
    ArchiveError,
    DuplicateEntryName,
    UnsafeEntryName,
    entry,
    iter_zip_entries,
    sanitize_entry_name,
    write_zip_to,
)


def _unzip(buffer: io.BytesIO) -> dict[str, bytes]:
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


# ── sanitize_entry_name ──────────────────────────────────────────────────

class TestSanitizeEntryName:
    def test_simple_name_unchanged(self):
        assert sanitize_entry_name("paper.pdf") == "paper.pdf"

    def test_nested_path_kept(self):
        assert sanitize_entry_name("2019/smith_paper.pdf") == "2019/smith_paper.pdf"

    def test_backslash_converted_to_forward_slash(self):
        assert sanitize_entry_name("2019\\smith_paper.pdf") == "2019/smith_paper.pdf"

    def test_leading_slash_stripped(self):
        assert sanitize_entry_name("/etc/passwd") == "etc/passwd"

    def test_double_slash_collapsed(self):
        assert sanitize_entry_name("a//b.pdf") == "a/b.pdf"

    @pytest.mark.parametrize(
        "bad",
        [
            "../../../etc/passwd",
            "a/../../b",
            "..",
            "a/..",
            "../a",
        ],
    )
    def test_dotdot_rejected(self, bad):
        """这是路径穿越的防线：调用方传什么名字都不该让解压时跳出目标目录。"""
        with pytest.raises(UnsafeEntryName):
            sanitize_entry_name(bad)

    @pytest.mark.parametrize("bad", ["a<b.pdf", "a>b.pdf", 'a"b.pdf', "a|b.pdf", "a?b.pdf", "a*b.pdf"])
    def test_windows_reserved_chars_rejected(self, bad):
        with pytest.raises(UnsafeEntryName):
            sanitize_entry_name(bad)

    def test_control_characters_rejected(self):
        with pytest.raises(UnsafeEntryName):
            sanitize_entry_name("a\x00b.pdf")

    def test_empty_name_rejected(self):
        with pytest.raises(UnsafeEntryName):
            sanitize_entry_name("")

    def test_only_slashes_rejected(self):
        with pytest.raises(UnsafeEntryName):
            sanitize_entry_name("///")

    def test_whitespace_padded_segment_rejected(self):
        with pytest.raises(UnsafeEntryName):
            sanitize_entry_name(" paper.pdf")

    def test_unicode_name_kept(self):
        assert sanitize_entry_name("深度学习与蛋白质折叠.pdf") == "深度学习与蛋白质折叠.pdf"

    def test_non_string_rejected(self):
        with pytest.raises(UnsafeEntryName):
            sanitize_entry_name(None)


# ── write_zip_to：基础 ───────────────────────────────────────────────────

class TestWriteZipBasic:
    def test_single_entry_bytes(self):
        buf = io.BytesIO()
        count = write_zip_to(buf, [entry("a.txt", b"hello")])
        assert count == 1
        assert _unzip(buf) == {"a.txt": b"hello"}

    def test_multiple_entries(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("a.txt", b"1"), entry("b.txt", b"2")])
        assert _unzip(buf) == {"a.txt": b"1", "b.txt": b"2"}

    def test_empty_entries_produces_valid_empty_zip(self):
        buf = io.BytesIO()
        assert write_zip_to(buf, []) == 0
        assert _unzip(buf) == {}

    def test_entries_can_be_a_generator(self):
        """调用方不需要先凑出完整列表——附件 ZIP 可能几百 MB。"""

        def gen():
            yield entry("a.txt", b"1")
            yield entry("b.txt", b"2")

        buf = io.BytesIO()
        assert write_zip_to(buf, gen()) == 2

    def test_nested_paths(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("2019/paper.pdf", b"%PDF-")])
        assert _unzip(buf) == {"2019/paper.pdf": b"%PDF-"}

    def test_unicode_content_and_name(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("标题.txt", "深度学习".encode())])
        assert _unzip(buf) == {"标题.txt": "深度学习".encode()}

    def test_does_not_close_destination(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("a.txt", b"1")])
        assert not buf.closed


class TestWriteZipContentForms:
    def test_bytes_content(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("a.txt", b"hello")])
        assert _unzip(buf)["a.txt"] == b"hello"

    def test_bytearray_content(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("a.txt", bytearray(b"hello"))])
        assert _unzip(buf)["a.txt"] == b"hello"

    def test_stream_content(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("a.txt", io.BytesIO(b"hello"))])
        assert _unzip(buf)["a.txt"] == b"hello"

    def test_iterable_chunks_content(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("a.txt", [b"hel", b"lo"])])
        assert _unzip(buf)["a.txt"] == b"hello"

    def test_large_stream_spans_chunks(self):
        payload = b"x" * ((1 << 20) * 2 + 7)
        buf = io.BytesIO()
        write_zip_to(buf, [entry("big.bin", io.BytesIO(payload))])
        assert _unzip(buf)["big.bin"] == payload

    def test_unsupported_content_type_raises(self):
        buf = io.BytesIO()
        with pytest.raises(TypeError):
            write_zip_to(buf, [entry("a.txt", 12345)])


class TestWriteZipDuplicates:
    def test_raise_by_default(self):
        buf = io.BytesIO()
        with pytest.raises(DuplicateEntryName):
            write_zip_to(buf, [entry("a.txt", b"1"), entry("a.txt", b"2")])

    def test_rename_mode_adds_suffix(self):
        buf = io.BytesIO()
        write_zip_to(
            buf,
            [entry("a.pdf", b"1"), entry("a.pdf", b"2"), entry("a.pdf", b"3")],
            on_duplicate="rename",
        )
        contents = _unzip(buf)
        assert set(contents) == {"a.pdf", "a (2).pdf", "a (3).pdf"}
        assert contents["a.pdf"] == b"1"
        assert contents["a (2).pdf"] == b"2"
        assert contents["a (3).pdf"] == b"3"

    def test_rename_mode_without_extension(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("README", b"1"), entry("README", b"2")], on_duplicate="rename")
        assert set(_unzip(buf)) == {"README", "README (2)"}

    def test_duplicate_detected_after_sanitization(self):
        """`a.pdf` 和 `/a.pdf` 规范化后是同一个名字，必须被当成重复。"""
        buf = io.BytesIO()
        with pytest.raises(DuplicateEntryName):
            write_zip_to(buf, [entry("a.pdf", b"1"), entry("/a.pdf", b"2")])

    def test_bad_on_duplicate_rejected(self):
        buf = io.BytesIO()
        with pytest.raises(ValueError):
            write_zip_to(buf, [entry("a.pdf", b"1")], on_duplicate="overwrite")


class TestWriteZipUnsafeNames:
    def test_traversal_attempt_rejected(self):
        """这是本模块真正的风险点：条目名来自文献标题/作者名，完全由调用方
        决定，必须在写的时候就把 .. 挡住。"""
        buf = io.BytesIO()
        with pytest.raises(UnsafeEntryName):
            write_zip_to(buf, [entry("../../../etc/passwd", b"pwned")])

    def test_rejection_happens_before_any_write(self):
        """坏条目之前已经写入的内容不该留在半成品 ZIP 里的假象——调用方应该
        整体重试，不是指望部分成功。这里验证的是异常确实抛出、调用方知道失败了。"""
        buf = io.BytesIO()
        with pytest.raises(UnsafeEntryName):
            write_zip_to(buf, [entry("good.txt", b"1"), entry("../bad", b"2")])


class TestEntryHelper:
    def test_entry_defaults_mtime_to_none(self):
        e = entry("a.txt", b"1")
        assert e.mtime is None

    def test_entry_accepts_custom_mtime(self):
        import datetime

        when = datetime.datetime(2019, 1, 1, tzinfo=datetime.UTC)
        assert entry("a.txt", b"1", mtime=when).mtime == when


class TestIterZipEntries:
    def test_lists_names_and_sizes(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("a.txt", b"hello"), entry("b/c.txt", b"hi")])
        buf.seek(0)
        listed = dict(iter_zip_entries(buf))
        assert listed == {"a.txt": 5, "b/c.txt": 2}

    def test_accepts_bytes_directly(self):
        buf = io.BytesIO()
        write_zip_to(buf, [entry("a.txt", b"hello")])
        listed = dict(iter_zip_entries(buf.getvalue()))
        assert listed == {"a.txt": 5}

    def test_empty_archive(self):
        buf = io.BytesIO()
        write_zip_to(buf, [])
        buf.seek(0)
        assert list(iter_zip_entries(buf)) == []


class TestCompressionRoundTrip:
    def test_stored_vs_deflated_same_content(self):
        payload = b"repeat " * 1000
        stored = io.BytesIO()
        write_zip_to(stored, [entry("a.bin", payload)], compression=zipfile.ZIP_STORED)
        deflated = io.BytesIO()
        write_zip_to(deflated, [entry("a.bin", payload)], compression=zipfile.ZIP_DEFLATED)
        assert _unzip(stored)["a.bin"] == payload == _unzip(deflated)["a.bin"]
        assert len(deflated.getvalue()) < len(stored.getvalue())


class TestArchiveErrorHierarchy:
    def test_unsafe_name_is_archive_error(self):
        assert issubclass(UnsafeEntryName, ArchiveError)

    def test_duplicate_is_archive_error(self):
        assert issubclass(DuplicateEntryName, ArchiveError)
