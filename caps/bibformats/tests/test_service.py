"""caps/bibformats 聚合/分发层（service.py）单测。"""

import pytest

from caps.bibformats import SUPPORTED_FORMATS, Record, UnsupportedFormat, parse, serialize


class TestParseDispatch:
    def test_ris(self):
        records = parse("ris", "TY  - JOUR\nTI  - X\nER  - \n")
        assert records[0].title == "X"

    def test_bibtex(self):
        records = parse("bibtex", "@article{x, title = {X}}")
        assert records[0].title == "X"

    def test_csljson(self):
        records = parse("csljson", '[{"id": "x", "title": "X"}]')
        assert records[0].title == "X"

    def test_unsupported_format_raises(self):
        with pytest.raises(UnsupportedFormat):
            parse("endnote-xml", "whatever")


class TestSerializeDispatch:
    def test_ris(self):
        out = serialize("ris", [Record(title="X")])
        assert "TI  - X" in out

    def test_bibtex(self):
        out = serialize("bibtex", [Record(title="X")])
        assert "title = {X}" in out

    def test_csljson(self):
        out = serialize("csljson", [Record(title="X")])
        assert '"title": "X"' in out

    def test_unsupported_format_raises(self):
        with pytest.raises(UnsupportedFormat):
            serialize("endnote-xml", [Record(title="X")])


class TestSupportedFormats:
    def test_exactly_three(self):
        assert SUPPORTED_FORMATS == {"ris", "bibtex", "csljson"}
