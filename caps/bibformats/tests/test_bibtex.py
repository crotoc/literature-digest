"""caps/bibformats BibTeX 解析/序列化单测。"""

from caps.bibformats.parsers.bibtex import parse_bibtex, serialize_bibtex
from caps.bibformats.record import Person, Record

SAMPLE_ARTICLE = """@article{smith2019great,
  author = {Smith, John and Doe, Jane},
  title = {A Great Paper},
  journal = {Nature},
  year = {2019},
  month = {5},
  volume = {567},
  number = {7748},
  pages = {100--110},
  publisher = {Nature Publishing Group},
  doi = {10.1038/s41586-019-1234-5},
  issn = {1476-4687},
  url = {https://example.com/paper},
  abstract = {This is the abstract.},
  language = {en},
  note = {A note.}
}
"""


class TestParseBibtexBasic:
    def test_parses_single_entry(self):
        assert len(parse_bibtex(SAMPLE_ARTICLE)) == 1

    def test_citekey(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].citekey == "smith2019great"

    def test_item_type(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].item_type == "journal_article"

    def test_authors_split_on_and(self):
        record = parse_bibtex(SAMPLE_ARTICLE)[0]
        assert record.authors == (
            Person(family="Smith", given="John"),
            Person(family="Doe", given="Jane"),
        )

    def test_title(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].title == "A Great Paper"

    def test_container_title_from_journal(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].container_title == "Nature"

    def test_year(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].year == 2019

    def test_month_numeric(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].month == 5

    def test_volume_and_issue(self):
        record = parse_bibtex(SAMPLE_ARTICLE)[0]
        assert record.volume == "567"
        assert record.issue == "7748"

    def test_pages_double_dash_normalized(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].pages == "100-110"

    def test_publisher(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].publisher == "Nature Publishing Group"

    def test_doi(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].doi == "10.1038/s41586-019-1234-5"

    def test_issn(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].issn == "1476-4687"

    def test_url(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].url == "https://example.com/paper"

    def test_abstract(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].abstract == "This is the abstract."

    def test_language(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].language == "en"

    def test_note(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].note == "A note."

    def test_bibtex_type_preserved_in_extra(self):
        assert parse_bibtex(SAMPLE_ARTICLE)[0].extra["bibtex_type"] == "article"


class TestParseBibtexNameHeuristics:
    def test_organization_author_braced(self):
        text = "@misc{x, author = {{World Health Organization}}, title = {Y}}"
        record = parse_bibtex(text)[0]
        assert record.authors == (Person(literal="World Health Organization"),)

    def test_single_author_no_and(self):
        text = "@misc{x, author = {Smith, John}}"
        record = parse_bibtex(text)[0]
        assert record.authors == (Person(family="Smith", given="John"),)


class TestParseBibtexMonthNames:
    def test_three_letter_abbreviation(self):
        text = "@article{x, month = {jan}}"
        assert parse_bibtex(text)[0].month == 1

    def test_full_name(self):
        text = "@article{x, month = {december}}"
        assert parse_bibtex(text)[0].month == 12

    def test_unrecognized_month_is_none(self):
        text = "@article{x, month = {whenever}}"
        assert parse_bibtex(text)[0].month is None


class TestParseBibtexEntryTypes:
    def test_book(self):
        assert parse_bibtex("@book{x, title={T}}")[0].item_type == "book"

    def test_inproceedings(self):
        assert parse_bibtex("@inproceedings{x, title={T}}")[0].item_type == "conference_paper"

    def test_phdthesis(self):
        assert parse_bibtex("@phdthesis{x, title={T}}")[0].item_type == "thesis"

    def test_techreport(self):
        assert parse_bibtex("@techreport{x, title={T}}")[0].item_type == "report"

    def test_unpublished(self):
        assert parse_bibtex("@unpublished{x, title={T}}")[0].item_type == "preprint"

    def test_misc_falls_back_to_webpage(self):
        assert parse_bibtex("@misc{x, title={T}}")[0].item_type == "webpage"

    def test_booktitle_used_for_inproceedings(self):
        text = "@inproceedings{x, booktitle = {Proceedings of Something}}"
        assert parse_bibtex(text)[0].container_title == "Proceedings of Something"

    def test_thesis_school_becomes_publisher(self):
        text = "@phdthesis{x, school = {MIT}}"
        assert parse_bibtex(text)[0].publisher == "MIT"


class TestParseBibtexMultipleEntries:
    def test_two_entries(self):
        text = SAMPLE_ARTICLE + "\n" + SAMPLE_ARTICLE.replace("smith2019great", "smith2019other")
        records = parse_bibtex(text)
        assert len(records) == 2
        assert records[0].citekey == "smith2019great"
        assert records[1].citekey == "smith2019other"

    def test_string_entries_skipped(self):
        text = '@string{nature = "Nature"}\n' + SAMPLE_ARTICLE
        records = parse_bibtex(text)
        assert len(records) == 1

    def test_comment_entries_skipped(self):
        text = "@comment{this is a comment}\n" + SAMPLE_ARTICLE
        records = parse_bibtex(text)
        assert len(records) == 1


class TestParseBibtexEdgeCases:
    def test_empty_string_returns_empty_list(self):
        assert parse_bibtex("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_bibtex("   \n  ") == []

    def test_quoted_value_instead_of_braces(self):
        text = '@article{x, title = "Quoted Title"}'
        assert parse_bibtex(text)[0].title == "Quoted Title"

    def test_nested_braces_preserved_as_literal_text(self):
        text = "@article{x, title = {A Study of {AI} Models}}"
        assert parse_bibtex(text)[0].title == "A Study of {AI} Models"

    def test_comma_inside_braced_field_not_split(self):
        text = "@article{x, author = {Smith, John}, title = {T}}"
        record = parse_bibtex(text)[0]
        assert record.authors == (Person(family="Smith", given="John"),)
        assert record.title == "T"

    def test_unmapped_field_preserved_in_extra(self):
        text = "@article{x, address = {Somewhere}}"
        record = parse_bibtex(text)[0]
        assert record.extra["address"] == "Somewhere"

    def test_unknown_entry_type_falls_back_to_journal_article(self):
        text = "@weirdtype{x, title = {T}}"
        record = parse_bibtex(text)[0]
        assert record.item_type == "journal_article"
        assert record.extra["bibtex_type"] == "weirdtype"


class TestSerializeBibtex:
    def test_round_trip_basic_fields(self):
        records = parse_bibtex(SAMPLE_ARTICLE)
        reparsed = parse_bibtex(serialize_bibtex(records))
        assert reparsed == records

    def test_serialize_empty_list(self):
        assert serialize_bibtex([]) == ""

    def test_output_has_entry_type_and_citekey(self):
        record = Record(item_type="journal_article", citekey="key1", title="X")
        out = serialize_bibtex([record])
        assert out.startswith("@article{key1,")

    def test_missing_citekey_falls_back_to_placeholder(self):
        record = Record(title="X")
        out = serialize_bibtex([record])
        assert out.startswith("@article{unknown,")

    def test_multiple_authors_joined_with_and(self):
        record = Record(
            authors=(Person(family="Smith", given="John"), Person(family="Doe", given="Jane"))
        )
        out = serialize_bibtex([record])
        assert "Smith, John and Doe, Jane" in out

    def test_pages_single_dash_converted_to_double(self):
        record = Record(pages="100-110")
        out = serialize_bibtex([record])
        assert "100--110" in out

    def test_book_chapter_uses_booktitle(self):
        record = Record(item_type="book_chapter", container_title="Big Book")
        out = serialize_bibtex([record])
        assert "booktitle = {Big Book}" in out

    def test_journal_article_uses_journal_key(self):
        record = Record(item_type="journal_article", container_title="Nature")
        out = serialize_bibtex([record])
        assert "journal = {Nature}" in out

    def test_preserves_original_bibtex_type_over_canonical_mapping(self):
        record = parse_bibtex("@conference{x, title={T}}")[0]
        out = serialize_bibtex([record])
        assert out.startswith("@conference{")

    def test_round_trip_multiple_entries(self):
        text = SAMPLE_ARTICLE + "\n" + SAMPLE_ARTICLE.replace("smith2019great", "smith2019other")
        records = parse_bibtex(text)
        reparsed = parse_bibtex(serialize_bibtex(records))
        assert reparsed == records

    def test_cross_format_bookkeeping_key_not_leaked_into_output(self):
        from caps.bibformats.parsers.ris import parse_ris

        record = parse_ris("TY  - JOUR\nTI  - X\nER  - \n")[0]  # extra 里有 ris_type
        out = serialize_bibtex([record])
        assert "ris_type" not in out

    def test_round_trip_with_extra_field(self):
        text = "@article{x, address = {Somewhere}, title = {T}}"
        records = parse_bibtex(text)
        reparsed = parse_bibtex(serialize_bibtex(records))
        assert reparsed == records
