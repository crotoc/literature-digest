"""caps/bibformats CSL-JSON 解析/序列化单测。"""

import json

import pytest

from caps.bibformats.parsers.csljson import parse_csljson, serialize_csljson
from caps.bibformats.record import ParseError, Person, Record

SAMPLE_ITEM = {
    "id": "smith2019great",
    "type": "article-journal",
    "title": "A Great Paper",
    "author": [{"family": "Smith", "given": "John"}, {"family": "Doe", "given": "Jane"}],
    "issued": {"date-parts": [[2019, 5, 12]]},
    "container-title": "Nature",
    "volume": "567",
    "issue": "7748",
    "page": "100-110",
    "publisher": "Nature Publishing Group",
    "DOI": "10.1038/s41586-019-1234-5",
    "ISSN": "1476-4687",
    "URL": "https://example.com/paper",
    "abstract": "This is the abstract.",
    "language": "en",
    "note": "A note.",
}


class TestParseCsljsonBasic:
    def test_parses_array_of_one(self):
        records = parse_csljson(json.dumps([SAMPLE_ITEM]))
        assert len(records) == 1

    def test_citekey(self):
        record = parse_csljson(json.dumps([SAMPLE_ITEM]))[0]
        assert record.citekey == "smith2019great"

    def test_item_type(self):
        record = parse_csljson(json.dumps([SAMPLE_ITEM]))[0]
        assert record.item_type == "journal_article"

    def test_title(self):
        assert parse_csljson(json.dumps([SAMPLE_ITEM]))[0].title == "A Great Paper"

    def test_authors(self):
        record = parse_csljson(json.dumps([SAMPLE_ITEM]))[0]
        assert record.authors == (
            Person(family="Smith", given="John"),
            Person(family="Doe", given="Jane"),
        )

    def test_date_parts(self):
        record = parse_csljson(json.dumps([SAMPLE_ITEM]))[0]
        assert (record.year, record.month, record.day) == (2019, 5, 12)

    def test_container_title(self):
        assert parse_csljson(json.dumps([SAMPLE_ITEM]))[0].container_title == "Nature"

    def test_volume_issue(self):
        record = parse_csljson(json.dumps([SAMPLE_ITEM]))[0]
        assert record.volume == "567"
        assert record.issue == "7748"

    def test_pages(self):
        assert parse_csljson(json.dumps([SAMPLE_ITEM]))[0].pages == "100-110"

    def test_publisher(self):
        assert parse_csljson(json.dumps([SAMPLE_ITEM]))[0].publisher == "Nature Publishing Group"

    def test_doi(self):
        assert parse_csljson(json.dumps([SAMPLE_ITEM]))[0].doi == "10.1038/s41586-019-1234-5"

    def test_issn(self):
        assert parse_csljson(json.dumps([SAMPLE_ITEM]))[0].issn == "1476-4687"

    def test_url(self):
        assert parse_csljson(json.dumps([SAMPLE_ITEM]))[0].url == "https://example.com/paper"

    def test_abstract(self):
        assert parse_csljson(json.dumps([SAMPLE_ITEM]))[0].abstract == "This is the abstract."

    def test_csl_type_preserved_in_extra(self):
        record = parse_csljson(json.dumps([SAMPLE_ITEM]))[0]
        assert record.extra["csl_type"] == "article-journal"


class TestParseCsljsonWrapping:
    def test_single_object_wrapped_as_list(self):
        records = parse_csljson(json.dumps(SAMPLE_ITEM))
        assert len(records) == 1
        assert records[0].title == "A Great Paper"

    def test_items_wrapper_key(self):
        text = json.dumps({"items": [SAMPLE_ITEM]})
        records = parse_csljson(text)
        assert len(records) == 1


class TestParseCsljsonEdgeCases:
    def test_empty_string_returns_empty_list(self):
        assert parse_csljson("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_csljson("   \n") == []

    def test_invalid_json_raises_parse_error(self):
        with pytest.raises(ParseError):
            parse_csljson("{not valid json")

    def test_top_level_number_raises_parse_error(self):
        with pytest.raises(ParseError):
            parse_csljson("42")

    def test_unrecognized_type_falls_back_to_journal_article(self):
        item = {"id": "x", "type": "legal_case", "title": "T"}
        record = parse_csljson(json.dumps([item]))[0]
        assert record.item_type == "journal_article"
        assert record.extra["csl_type"] == "legal_case"

    def test_literal_author(self):
        item = {"id": "x", "author": [{"literal": "World Health Organization"}]}
        record = parse_csljson(json.dumps([item]))[0]
        assert record.authors == (Person(literal="World Health Organization"),)

    def test_issued_raw_fallback(self):
        item = {"id": "x", "issued": {"raw": "circa 2019"}}
        record = parse_csljson(json.dumps([item]))[0]
        assert record.year == 2019

    def test_issued_date_parts_year_only(self):
        item = {"id": "x", "issued": {"date-parts": [[2021]]}}
        record = parse_csljson(json.dumps([item]))[0]
        assert (record.year, record.month, record.day) == (2021, None, None)

    def test_missing_issued_gives_none_date(self):
        item = {"id": "x"}
        record = parse_csljson(json.dumps([item]))[0]
        assert record.year is None

    def test_missing_id_gives_none_citekey(self):
        item = {"title": "No ID here"}
        record = parse_csljson(json.dumps([item]))[0]
        assert record.citekey is None

    def test_unmapped_key_preserved_in_extra(self):
        item = {"id": "x", "event": "Some Conference 2019"}
        record = parse_csljson(json.dumps([item]))[0]
        assert record.extra["event"] == "Some Conference 2019"

    def test_pmid_field(self):
        item = {"id": "x", "PMID": "12345678"}
        record = parse_csljson(json.dumps([item]))[0]
        assert record.pmid == "12345678"


class TestParseCsljsonMultipleItems:
    def test_two_items(self):
        item2 = dict(SAMPLE_ITEM, id="other")
        records = parse_csljson(json.dumps([SAMPLE_ITEM, item2]))
        assert len(records) == 2
        assert records[0].citekey == "smith2019great"
        assert records[1].citekey == "other"


class TestSerializeCsljson:
    def test_round_trip_basic_fields(self):
        records = parse_csljson(json.dumps([SAMPLE_ITEM]))
        reparsed = parse_csljson(serialize_csljson(records))
        assert reparsed == records

    def test_serialize_empty_list(self):
        assert json.loads(serialize_csljson([])) == []

    def test_output_is_valid_json_array(self):
        record = Record(title="X")
        out = serialize_csljson([record])
        parsed = json.loads(out)
        assert isinstance(parsed, list)
        assert parsed[0]["title"] == "X"

    def test_missing_citekey_becomes_empty_id(self):
        record = Record(title="X")
        out = json.loads(serialize_csljson([record]))[0]
        assert out["id"] == ""

    def test_authors_serialized_as_family_given_dicts(self):
        record = Record(authors=(Person(family="Smith", given="John"),))
        out = json.loads(serialize_csljson([record]))[0]
        assert out["author"] == [{"family": "Smith", "given": "John"}]

    def test_literal_author_serialized_with_literal_key(self):
        record = Record(authors=(Person(literal="World Health Organization"),))
        out = json.loads(serialize_csljson([record]))[0]
        assert out["author"] == [{"literal": "World Health Organization"}]

    def test_year_only_date_parts(self):
        record = Record(year=2020)
        out = json.loads(serialize_csljson([record]))[0]
        assert out["issued"] == {"date-parts": [[2020]]}

    def test_full_date_parts(self):
        record = Record(year=2020, month=3, day=15)
        out = json.loads(serialize_csljson([record]))[0]
        assert out["issued"] == {"date-parts": [[2020, 3, 15]]}

    def test_no_year_omits_issued_key(self):
        record = Record(title="X")
        out = json.loads(serialize_csljson([record]))[0]
        assert "issued" not in out

    def test_preserves_original_csl_type_over_canonical_mapping(self):
        record = parse_csljson(json.dumps([{"id": "x", "type": "legal_case"}]))[0]
        out = json.loads(serialize_csljson([record]))[0]
        assert out["type"] == "legal_case"

    def test_unicode_not_escaped(self):
        record = Record(title="深度学习与蛋白质折叠")
        out = serialize_csljson([record])
        assert "深度学习与蛋白质折叠" in out

    def test_round_trip_multiple_items(self):
        item2 = dict(SAMPLE_ITEM, id="other")
        records = parse_csljson(json.dumps([SAMPLE_ITEM, item2]))
        reparsed = parse_csljson(serialize_csljson(records))
        assert reparsed == records

    def test_cross_format_bookkeeping_key_not_leaked_into_output(self):
        from caps.bibformats.parsers.bibtex import parse_bibtex

        record = parse_bibtex("@article{x, title = {T}}")[0]  # extra 里有 bibtex_type
        out = json.loads(serialize_csljson([record]))[0]
        assert "bibtex_type" not in out

    def test_round_trip_with_extra_field(self):
        item = {"id": "x", "event": "Some Conference 2019", "title": "T"}
        records = parse_csljson(json.dumps([item]))
        reparsed = parse_csljson(serialize_csljson(records))
        assert reparsed == records
