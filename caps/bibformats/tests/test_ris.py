"""caps/bibformats RIS 解析/序列化单测。"""

import pytest

from caps.bibformats.parsers.ris import parse_ris, serialize_ris
from caps.bibformats.record import ParseError, Person, Record

SAMPLE_JOUR = """TY  - JOUR
AU  - Smith, John
AU  - Doe, Jane
TI  - A Great Paper
T2  - Nature
PY  - 2019/05/12/
VL  - 567
IS  - 7748
SP  - 100
EP  - 110
PB  - Nature Publishing Group
DO  - 10.1038/s41586-019-1234-5
SN  - 1476-4687
UR  - https://example.com/paper
AB  - This is the abstract.
LA  - en
N1  - A note.
ER  -
"""


class TestParseRisBasic:
    def test_parses_single_record(self):
        records = parse_ris(SAMPLE_JOUR)
        assert len(records) == 1

    def test_item_type(self):
        records = parse_ris(SAMPLE_JOUR)
        assert records[0].item_type == "journal_article"

    def test_authors(self):
        records = parse_ris(SAMPLE_JOUR)
        assert records[0].authors == (
            Person(family="Smith", given="John"),
            Person(family="Doe", given="Jane"),
        )

    def test_title(self):
        assert parse_ris(SAMPLE_JOUR)[0].title == "A Great Paper"

    def test_container_title(self):
        assert parse_ris(SAMPLE_JOUR)[0].container_title == "Nature"

    def test_date(self):
        record = parse_ris(SAMPLE_JOUR)[0]
        assert (record.year, record.month, record.day) == (2019, 5, 12)

    def test_volume_issue(self):
        record = parse_ris(SAMPLE_JOUR)[0]
        assert record.volume == "567"
        assert record.issue == "7748"

    def test_pages_combined_from_sp_ep(self):
        assert parse_ris(SAMPLE_JOUR)[0].pages == "100-110"

    def test_publisher(self):
        assert parse_ris(SAMPLE_JOUR)[0].publisher == "Nature Publishing Group"

    def test_doi(self):
        assert parse_ris(SAMPLE_JOUR)[0].doi == "10.1038/s41586-019-1234-5"

    def test_issn_for_journal(self):
        record = parse_ris(SAMPLE_JOUR)[0]
        assert record.issn == "1476-4687"
        assert record.isbn is None

    def test_url(self):
        assert parse_ris(SAMPLE_JOUR)[0].url == "https://example.com/paper"

    def test_abstract(self):
        assert parse_ris(SAMPLE_JOUR)[0].abstract == "This is the abstract."

    def test_language(self):
        assert parse_ris(SAMPLE_JOUR)[0].language == "en"

    def test_note(self):
        assert parse_ris(SAMPLE_JOUR)[0].note == "A note."

    def test_ris_type_preserved_in_extra(self):
        assert parse_ris(SAMPLE_JOUR)[0].extra["ris_type"] == "JOUR"


class TestParseRisMultipleRecords:
    def test_two_records(self):
        text = SAMPLE_JOUR + "\n" + SAMPLE_JOUR
        records = parse_ris(text)
        assert len(records) == 2
        assert records[0].title == records[1].title == "A Great Paper"


class TestParseRisEdgeCases:
    def test_empty_string_returns_empty_list(self):
        assert parse_ris("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_ris("   \n  \n") == []

    def test_book_type_maps_sn_to_isbn(self):
        text = "TY  - BOOK\nTI  - A Book\nSN  - 978-0-123456-78-9\nER  - \n"
        record = parse_ris(text)[0]
        assert record.isbn == "978-0-123456-78-9"
        assert record.issn is None

    def test_a1_used_when_au_absent(self):
        text = "TY  - JOUR\nA1  - Smith, John\nER  - \n"
        record = parse_ris(text)[0]
        assert record.authors == (Person(family="Smith", given="John"),)

    def test_t1_used_when_ti_absent(self):
        text = "TY  - JOUR\nT1  - Alt Title\nER  - \n"
        assert parse_ris(text)[0].title == "Alt Title"

    def test_unknown_tag_preserved_in_extra(self):
        text = "TY  - JOUR\nTI  - X\nKW  - keyword one\nER  - \n"
        record = parse_ris(text)[0]
        assert record.extra["KW"] == "keyword one"

    def test_multiple_values_of_unknown_tag_become_list(self):
        text = "TY  - JOUR\nKW  - kw1\nKW  - kw2\nER  - \n"
        record = parse_ris(text)[0]
        assert record.extra["KW"] == ["kw1", "kw2"]

    def test_second_ur_preserved_in_extra(self):
        text = "TY  - JOUR\nUR  - https://a.example\nUR  - https://b.example\nER  - \n"
        record = parse_ris(text)[0]
        assert record.url == "https://a.example"
        assert record.extra["extra_urls"] == ["https://b.example"]

    def test_unrecognized_ty_falls_back_to_journal_article(self):
        text = "TY  - SOMETHINGWEIRD\nTI  - X\nER  - \n"
        record = parse_ris(text)[0]
        assert record.item_type == "journal_article"
        assert record.extra["ris_type"] == "SOMETHINGWEIRD"

    def test_wrapped_continuation_line_appended_to_previous_tag(self):
        text = "TY  - JOUR\nAB  - First part\nof the abstract.\nER  - \n"
        record = parse_ris(text)[0]
        assert record.abstract == "First part of the abstract."

    def test_only_year_no_month_day(self):
        text = "TY  - JOUR\nPY  - 2020\nER  - \n"
        record = parse_ris(text)[0]
        assert (record.year, record.month, record.day) == (2020, None, None)

    def test_date_missing_entirely(self):
        text = "TY  - JOUR\nTI  - X\nER  - \n"
        record = parse_ris(text)[0]
        assert record.year is None


class TestParseRisErrors:
    def test_tag_before_ty_raises(self):
        with pytest.raises(ParseError):
            parse_ris("TI  - Orphan title\nER  - \n")

    def test_non_tag_line_before_any_tag_raises(self):
        with pytest.raises(ParseError):
            parse_ris("this is not a tag line at all\n")

    def test_missing_er_raises(self):
        with pytest.raises(ParseError):
            parse_ris("TY  - JOUR\nTI  - X\n")


class TestSerializeRis:
    def test_round_trip_basic_fields(self):
        records = parse_ris(SAMPLE_JOUR)
        reparsed = parse_ris(serialize_ris(records))
        assert reparsed == records

    def test_serialize_empty_list(self):
        assert serialize_ris([]) == ""

    def test_output_starts_with_ty_ends_with_er(self):
        record = Record(item_type="journal_article", title="X")
        out = serialize_ris([record])
        lines = out.strip().splitlines()
        assert lines[0].startswith("TY")
        assert lines[-1].startswith("ER")

    def test_multiple_authors_serialized_as_separate_au_lines(self):
        record = Record(
            authors=(Person(family="Smith", given="John"), Person(family="Doe", given="Jane"))
        )
        out = serialize_ris([record])
        assert out.count("AU  - ") == 2

    def test_book_uses_bt_tag_for_container_title(self):
        record = Record(item_type="book", container_title="Big Book", title="Chapter X")
        out = serialize_ris([record])
        assert "BT  - Big Book" in out
        assert "T2  - Big Book" not in out

    def test_preserves_original_ris_type_over_canonical_mapping(self):
        record = parse_ris("TY  - CPAPER\nTI  - X\nER  - \n")[0]
        out = serialize_ris([record])
        assert "TY  - CPAPER" in out

    def test_isbn_emitted_as_sn_for_book(self):
        record = Record(item_type="book", isbn="978-0-000000-00-0")
        out = serialize_ris([record])
        assert "SN  - 978-0-000000-00-0" in out

    def test_round_trip_multiple_records(self):
        records = parse_ris(SAMPLE_JOUR + "\n" + SAMPLE_JOUR)
        reparsed = parse_ris(serialize_ris(records))
        assert reparsed == records

    def test_cross_format_bookkeeping_key_not_leaked_into_output(self):
        from caps.bibformats.parsers.bibtex import parse_bibtex

        record = parse_bibtex("@article{x, title = {T}}")[0]  # extra 里有 bibtex_type
        out = serialize_ris([record])
        assert "bibtex_type" not in out

    def test_round_trip_with_unknown_extra_tag(self):
        text = "TY  - JOUR\nTI  - X\nKW  - kw1\nKW  - kw2\nER  - \n"
        records = parse_ris(text)
        reparsed = parse_ris(serialize_ris(records))
        assert reparsed == records
