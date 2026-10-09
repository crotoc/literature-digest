"""caps/bibformats 共享模型（record.py）单测。"""

from caps.bibformats.record import Person, format_person, join_pages, parse_person, split_pages


class TestParsePerson:
    def test_family_comma_given(self):
        assert parse_person("Smith, John") == Person(family="Smith", given="John")

    def test_given_family_no_comma(self):
        assert parse_person("John Smith") == Person(given="John", family="Smith")

    def test_multi_word_given_name(self):
        assert parse_person("John van der Berg") == Person(given="John van der", family="Berg")

    def test_braced_literal_is_organization(self):
        assert parse_person("{World Health Organization}") == Person(
            literal="World Health Organization"
        )

    def test_single_word_no_comma_is_literal(self):
        assert parse_person("Prince") == Person(literal="Prince")

    def test_strips_whitespace(self):
        assert parse_person("  Smith ,  John  ") == Person(family="Smith", given="John")

    def test_empty_string(self):
        assert parse_person("") == Person(literal="")

    def test_comma_with_empty_given(self):
        assert parse_person("Smith,") == Person(family="Smith", given=None)


class TestFormatPerson:
    def test_family_given(self):
        assert format_person(Person(family="Smith", given="John")) == "Smith, John"

    def test_literal_preferred_over_split_fields(self):
        assert format_person(Person(literal="Org Name")) == "Org Name"

    def test_family_only(self):
        assert format_person(Person(family="Smith")) == "Smith"

    def test_given_only(self):
        assert format_person(Person(given="John")) == "John"

    def test_round_trip_family_given(self):
        person = parse_person("Smith, John")
        assert format_person(person) == "Smith, John"


class TestSplitPages:
    def test_range(self):
        assert split_pages("100-110") == ("100", "110")

    def test_single_page(self):
        assert split_pages("100") == ("100", None)

    def test_none(self):
        assert split_pages(None) == (None, None)

    def test_empty_string(self):
        assert split_pages("") == (None, None)

    def test_strips_whitespace(self):
        assert split_pages(" 100 - 110 ") == ("100", "110")


class TestJoinPages:
    def test_both(self):
        assert join_pages("100", "110") == "100-110"

    def test_start_only(self):
        assert join_pages("100", None) == "100"

    def test_end_only(self):
        assert join_pages(None, "110") == "110"

    def test_neither(self):
        assert join_pages(None, None) is None

    def test_round_trip(self):
        start, end = split_pages("100-110")
        assert join_pages(start, end) == "100-110"


class TestRecordDefaults:
    def test_default_item_type(self):
        from caps.bibformats.record import Record

        assert Record().item_type == "journal_article"

    def test_default_authors_empty_tuple(self):
        from caps.bibformats.record import Record

        assert Record().authors == ()

    def test_extra_defaults_to_empty_dict_not_shared(self):
        from caps.bibformats.record import Record

        a = Record()
        b = Record()
        assert a.extra == {} and b.extra == {}
        assert a.extra is not b.extra

    def test_is_frozen(self):
        import pytest

        from caps.bibformats.record import Record

        record = Record(title="x")
        with pytest.raises(AttributeError):
            record.title = "y"
