"""caps/citation 单测。不建库、不联网。"""

import pytest

from caps.bibformats import Person, Record
from caps.citation import (
    InvalidCitekey,
    UnknownStyle,
    generate_bibtex_key,
    latex_cite,
    list_styles,
    render_apa,
    render_citation,
    render_vancouver,
)

SAMPLE = Record(
    title="A Great Paper",
    authors=(Person(family="Smith", given="John"), Person(family="Doe", given="Jane")),
    year=2019,
    container_title="Nature",
    volume="567",
    issue="7748",
    pages="100-110",
    doi="10.1038/s41586-019-1234-5",
)


class TestRenderApa:
    def test_full_record(self):
        out = render_apa(SAMPLE)
        assert out == (
            "Smith, J., & Doe, J. (2019). A Great Paper. Nature, 567(7748), "
            "100-110. https://doi.org/10.1038/s41586-019-1234-5"
        )

    def test_single_author(self):
        record = Record(title="Solo Work", authors=(Person(family="Smith", given="John"),), year=2020)
        assert render_apa(record) == "Smith, J. (2020). Solo Work."

    def test_three_authors_oxford_comma(self):
        record = Record(
            title="T",
            authors=(
                Person(family="A", given="X"),
                Person(family="B", given="Y"),
                Person(family="C", given="Z"),
            ),
            year=2021,
        )
        assert render_apa(record) == "A, X., B, Y., & C, Z. (2021). T."

    def test_no_authors_no_year(self):
        record = Record(title="Mystery")
        assert render_apa(record) == "(n.d.). Mystery."

    def test_organization_author_literal(self):
        record = Record(title="Report", authors=(Person(literal="World Health Organization"),), year=2022)
        assert render_apa(record) == "World Health Organization (2022). Report."

    def test_url_used_when_no_doi(self):
        record = Record(title="T", url="https://example.com/x")
        assert render_apa(record) == "(n.d.). T. https://example.com/x"

    def test_doi_preferred_over_url(self):
        record = Record(title="T", doi="10.1/x", url="https://example.com/x")
        out = render_apa(record)
        assert "doi.org/10.1/x" in out
        assert "example.com" not in out

    def test_multi_word_given_name_initials(self):
        record = Record(title="T", authors=(Person(family="Berg", given="John Robert"),))
        assert render_apa(record) == "Berg, J.R. (n.d.). T."

    def test_volume_without_issue(self):
        record = Record(title="T", container_title="Nature", volume="567", year=2019)
        assert render_apa(record) == "(2019). T. Nature, 567."

    def test_no_container_title(self):
        record = Record(title="T", year=2019)
        assert render_apa(record) == "(2019). T."


class TestRenderVancouver:
    def test_full_record(self):
        out = render_vancouver(SAMPLE)
        assert out == "Smith J, Doe J. A Great Paper. Nature. 2019;567(7748):100-110."

    def test_single_author(self):
        record = Record(title="Solo Work", authors=(Person(family="Smith", given="John"),), year=2020)
        assert render_vancouver(record) == "Smith J. Solo Work. 2020."

    def test_more_than_six_authors_truncated_with_et_al(self):
        authors = tuple(Person(family=f"Author{i}", given="X") for i in range(8))
        record = Record(title="T", authors=authors)
        out = render_vancouver(record)
        assert out.startswith("Author0 X, Author1 X, Author2 X, Author3 X, Author4 X, Author5 X, et al.")

    def test_exactly_six_authors_no_et_al(self):
        authors = tuple(Person(family=f"Author{i}", given="X") for i in range(6))
        record = Record(title="T", authors=authors)
        out = render_vancouver(record)
        assert "et al" not in out

    def test_no_authors(self):
        record = Record(title="T", year=2019)
        assert render_vancouver(record) == "T. 2019."

    def test_organization_author_literal(self):
        record = Record(title="Report", authors=(Person(literal="WHO"),), year=2022)
        assert render_vancouver(record) == "WHO. Report. 2022."

    def test_vancouver_initials_have_no_periods(self):
        record = Record(title="T", authors=(Person(family="Berg", given="John Robert"),))
        assert "JR" in render_vancouver(record)
        assert "J.R." not in render_vancouver(record)


class TestRenderCitationDispatch:
    def test_apa_dispatch(self):
        assert render_citation(SAMPLE, "apa") == render_apa(SAMPLE)

    def test_vancouver_dispatch(self):
        assert render_citation(SAMPLE, "vancouver") == render_vancouver(SAMPLE)

    def test_default_style_is_apa(self):
        assert render_citation(SAMPLE) == render_apa(SAMPLE)

    def test_unknown_style_raises(self):
        with pytest.raises(UnknownStyle):
            render_citation(SAMPLE, "mla")


class TestListStyles:
    def test_contains_apa_and_vancouver(self):
        assert list_styles() == {"apa", "vancouver"}


class TestGenerateBibtexKey:
    def test_basic(self):
        assert generate_bibtex_key(SAMPLE) == "smith2019great"

    def test_skips_leading_stopword_in_title(self):
        record = Record(title="The Great Paper", authors=(Person(family="Smith", given="J"),), year=2019)
        assert generate_bibtex_key(record) == "smith2019great"

    def test_no_authors_uses_anon(self):
        record = Record(title="T", year=2020)
        assert generate_bibtex_key(record).startswith("anon2020")

    def test_no_year_omits_year_segment(self):
        record = Record(title="Great Paper", authors=(Person(family="Smith", given="J"),))
        assert generate_bibtex_key(record) == "smithgreat"

    def test_no_title_omits_title_segment(self):
        record = Record(authors=(Person(family="Smith", given="J"),), year=2019)
        assert generate_bibtex_key(record) == "smith2019"

    def test_organization_author_literal_used(self):
        record = Record(title="Report", authors=(Person(literal="World Health Organization"),), year=2022)
        key = generate_bibtex_key(record)
        assert key.startswith("worldhealthorganization2022")

    def test_lowercased(self):
        record = Record(title="BIG TITLE", authors=(Person(family="SMITH", given="J"),), year=2019)
        assert generate_bibtex_key(record) == generate_bibtex_key(record).lower()

    def test_unicode_family_name_preserved(self):
        record = Record(title="Paper", authors=(Person(family="穆勒", given="J"),), year=2019)
        key = generate_bibtex_key(record)
        assert key.startswith("穆勒2019")

    def test_collision_gets_letter_suffix(self):
        base = generate_bibtex_key(SAMPLE)
        key = generate_bibtex_key(SAMPLE, existing_keys={base})
        assert key == f"{base}a"

    def test_multiple_collisions_increment_suffix(self):
        base = generate_bibtex_key(SAMPLE)
        key = generate_bibtex_key(SAMPLE, existing_keys={base, f"{base}a", f"{base}b"})
        assert key == f"{base}c"

    def test_27th_collision_uses_double_letter(self):
        base = generate_bibtex_key(SAMPLE)
        existing = {base} | {f"{base}{chr(ord('a') + i)}" for i in range(26)}
        key = generate_bibtex_key(SAMPLE, existing_keys=existing)
        assert key == f"{base}aa"

    def test_all_stopword_title_omits_title_segment(self):
        record = Record(title="The Of A", authors=(Person(family="Smith", given="J"),), year=2019)
        assert generate_bibtex_key(record) == "smith2019"

    def test_result_has_no_separators(self):
        key = generate_bibtex_key(SAMPLE)
        assert " " not in key and "_" not in key and "-" not in key


class TestLatexCite:
    def test_single_key_as_string(self):
        assert latex_cite("smith2019") == "\\cite{smith2019}"

    def test_multiple_keys_as_list(self):
        assert latex_cite(["a2019", "b2020"]) == "\\cite{a2019,b2020}"

    def test_custom_command(self):
        assert latex_cite("a2019", command="citep") == "\\citep{a2019}"

    def test_empty_list_raises(self):
        with pytest.raises(InvalidCitekey):
            latex_cite([])

    def test_empty_string_key_raises(self):
        with pytest.raises(InvalidCitekey):
            latex_cite("")

    def test_key_with_brace_raises(self):
        with pytest.raises(InvalidCitekey):
            latex_cite("smith{2019}")

    def test_key_with_comma_raises(self):
        with pytest.raises(InvalidCitekey):
            latex_cite("smith,2019")

    def test_key_with_backslash_raises(self):
        with pytest.raises(InvalidCitekey):
            latex_cite("smith\\2019")

    def test_key_with_whitespace_raises(self):
        with pytest.raises(InvalidCitekey):
            latex_cite("smith 2019")

    def test_generated_key_is_always_latex_safe(self):
        # 端到端：citekey 生成器的输出永远能喂给 latex_cite，不需要再校验一遍
        key = generate_bibtex_key(SAMPLE)
        assert latex_cite(key) == f"\\cite{{{key}}}"
