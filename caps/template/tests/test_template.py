"""caps/template 单测。纯函数，不建库、不联网。"""

import pytest

from caps.template import (
    MissingValue,
    OutputTooLarge,
    TemplateError,
    TemplateSyntaxInvalid,
    UnknownPlaceholder,
    iter_placeholders,
    jinja_variables,
    render_bracket,
    render_jinja,
    validate_bracket_template,
)

# ── 方括号：基本渲染 ────────────────────────────────────────────────────────

class TestBracketBasic:
    def test_simple_substitution(self):
        assert render_bracket("[year]", {"year": "2019"}) == "2019"

    def test_multiple_placeholders(self):
        # 两个占位都全小写，按大小写规则输出也全小写（这条规则本身在
        # TestBracketCase 里单独测）；这里只验证"多个占位都被替换了"。
        out = render_bracket("[firstauthor]_[year]", {"firstauthor": "Smith", "year": "2019"})
        assert out == "smith_2019"

    def test_literal_text_passes_through(self):
        assert render_bracket("paper-[year].pdf", {"year": "2019"}) == "paper-2019.pdf"

    def test_no_placeholders(self):
        assert render_bracket("no placeholders here", {}) == "no placeholders here"

    def test_keys_case_insensitive(self):
        assert render_bracket("[Year]", {"YEAR": "2019"}) == "2019"
        assert render_bracket("[YEAR]", {"year": "2019"}) == "2019"

    def test_escaped_brackets(self):
        assert render_bracket("[[literal]]", {}) == "[literal]"

    def test_escaped_brackets_mixed_with_real(self):
        out = render_bracket("[[not_a_var]] [year]", {"year": "2019"})
        assert out == "[not_a_var] 2019"


class TestBracketCase:
    def test_lowercase_key_lowercase_output(self):
        assert render_bracket("[year]", {"year": "ABCD"}) == "abcd"

    def test_uppercase_key_uppercase_output(self):
        assert render_bracket("[YEAR]", {"year": "abcd"}) == "ABCD"

    def test_title_case_key_title_output(self):
        assert render_bracket("[Title]", {"title": "attention is all you need"}) == (
            "Attention Is All You Need"
        )

    def test_mixed_case_key_untouched(self):
        """混写当作显式的"别碰大小写"。"""
        assert render_bracket("[firstAuthor]", {"firstauthor": "SmITh"}) == "SmITh"

    def test_firstauthor_all_caps(self):
        assert render_bracket("[FIRSTAUTHOR]", {"firstauthor": "smith"}) == "SMITH"


class TestBracketLimit:
    def test_string_limit_truncates_chars(self):
        assert render_bracket("[Title:5]", {"title": "attention is all"}) == "Atten"

    def test_list_limit_takes_first_n_items(self):
        out = render_bracket("[AUTHORS:2]", {"authors": ["Smith", "Lee", "Chen"]})
        assert out == "SMITH_LEE"

    def test_list_no_limit_takes_all(self):
        out = render_bracket("[authors]", {"authors": ["Smith", "Lee"]})
        assert out == "smith_lee"

    def test_custom_separator(self):
        out = render_bracket("[authors]", {"authors": ["Smith", "Lee"]}, list_separator="-")
        assert out == "smith-lee"

    def test_limit_larger_than_value(self):
        assert render_bracket("[Title:500]", {"title": "short"}) == "Short"


class TestBracketMissing:
    def test_raise_by_default(self):
        with pytest.raises(MissingValue) as caught:
            render_bracket("[year]", {})
        assert caught.value.key == "year"

    def test_empty_mode(self):
        assert render_bracket("[FIRSTAUTHOR]_[year]", {"year": "2019"}, on_missing="empty") == "_2019"

    def test_keep_mode_for_preview(self):
        out = render_bracket("[FIRSTAUTHOR]_[year]", {"year": "2019"}, on_missing="keep")
        assert out == "[FIRSTAUTHOR]_2019"

    def test_empty_string_value_counts_as_missing(self):
        with pytest.raises(MissingValue):
            render_bracket("[title]", {"title": ""})

    def test_empty_list_value_counts_as_missing(self):
        with pytest.raises(MissingValue):
            render_bracket("[authors]", {"authors": []})

    def test_none_value_counts_as_missing(self):
        with pytest.raises(MissingValue):
            render_bracket("[title]", {"title": None})

    def test_bad_on_missing_rejected(self):
        with pytest.raises(ValueError):
            render_bracket("[year]", {"year": "x"}, on_missing="explode")


class TestBracketNoExecutionSemantics:
    """方括号渲染器只做字典查表，没有任何执行语义——这是它能安全接受用户填的
    模板的理由。"""

    def test_dunder_key_does_not_even_match_the_placeholder_pattern(self):
        """占位名必须以字母开头（`[A-Za-z][A-Za-z0-9_]*`），所以 `__class__`
        这种以下划线开头的名字**根本不会被当成占位**，原样留在输出里——
        不是"查到了但没有特殊处理"，而是"连查表的资格都没有"。"""
        out = render_bracket("[__class__]", {"__class__": "whatever"}, on_missing="empty")
        assert out == "[__class__]"
        assert "whatever" not in out

    def test_unmatched_key_is_literal_not_error_if_not_bracketed(self):
        assert render_bracket("__class__", {}) == "__class__"

    def test_arbitrary_python_syntax_is_inert(self):
        out = render_bracket("[title]", {"title": "{{7*7}}"})
        assert out == "{{7*7}}"


class TestIterPlaceholders:
    def test_lists_placeholders_in_order(self):
        names = [p.key for p in iter_placeholders("[FIRSTAUTHOR:1]_[year]_[Title:40]")]
        assert names == ["FIRSTAUTHOR", "year", "Title"]

    def test_dedupes_same_key_and_limit(self):
        placeholders = iter_placeholders("[year]_[year]")
        assert len(placeholders) == 1

    def test_same_key_different_limit_both_listed(self):
        placeholders = iter_placeholders("[Title:10]_[Title:40]")
        assert len(placeholders) == 2

    def test_ignores_escaped_brackets(self):
        assert iter_placeholders("[[literal]] [year]") == [
            p for p in iter_placeholders("[year]")
        ]

    def test_empty_template(self):
        assert iter_placeholders("") == []

    def test_limit_captured(self):
        placeholders = iter_placeholders("[Title:40]")
        assert placeholders[0].limit == 40
        assert placeholders[0].key == "Title"

    def test_normalized_key_is_lowercase(self):
        assert iter_placeholders("[FIRSTAUTHOR]")[0].normalized_key == "firstauthor"


class TestValidateBracketTemplate:
    def test_passes_when_all_known(self):
        validate_bracket_template("[year]_[title]", allowed_keys=["year", "title", "firstauthor"])

    def test_raises_on_unknown(self):
        with pytest.raises(UnknownPlaceholder) as caught:
            validate_bracket_template("[yera]", allowed_keys=["year"])
        assert "yera" in caught.value.keys

    def test_case_insensitive_check(self):
        validate_bracket_template("[YEAR]", allowed_keys=["year"])

    def test_reports_all_unknowns_not_just_first(self):
        with pytest.raises(UnknownPlaceholder) as caught:
            validate_bracket_template("[aaa]_[bbb]", allowed_keys=["year"])
        assert set(caught.value.keys) == {"aaa", "bbb"}

    def test_empty_template_always_passes(self):
        validate_bracket_template("", allowed_keys=[])


# ── Jinja ───────────────────────────────────────────────────────────────

class TestJinjaBasic:
    def test_simple_substitution(self):
        assert render_jinja("Title: {{ title }}", {"title": "A paper"}) == "Title: A paper"

    def test_conditional(self):
        template = "{% if abstract %}Abstract: {{ abstract }}{% else %}No abstract{% endif %}"
        assert render_jinja(template, {"abstract": "x"}) == "Abstract: x"
        assert render_jinja(template, {"abstract": ""}) == "No abstract"

    def test_loop(self):
        template = "{% for a in authors %}{{ a }}, {% endfor %}"
        assert render_jinja(template, {"authors": ["Smith", "Lee"]}) == "Smith, Lee, "

    def test_no_placeholders(self):
        assert render_jinja("plain text", {}) == "plain text"

    def test_trailing_newline_kept(self):
        assert render_jinja("line\n", {}) == "line\n"

    def test_unicode(self):
        assert render_jinja("标题：{{ title }}", {"title": "深度学习"}) == "标题：深度学习"


class TestJinjaMissingIsStrict:
    def test_undefined_variable_raises(self):
        """StrictUndefined：拼错变量名报错，不是静默渲染成空串。"""
        with pytest.raises(MissingValue) as caught:
            render_jinja("{{ titel }}", {"title": "x"})
        assert caught.value.key == "titel"

    def test_defined_variable_works(self):
        assert render_jinja("{{ title }}", {"title": "x"}) == "x"


class TestJinjaSyntaxErrors:
    def test_unclosed_tag_raises_syntax_invalid(self):
        with pytest.raises(TemplateSyntaxInvalid):
            render_jinja("{% if x %}no endif", {"x": True})

    def test_bad_expression_raises_syntax_invalid(self):
        with pytest.raises(TemplateSyntaxInvalid):
            render_jinja("{{ ) }}", {})

    def test_syntax_error_reports_line(self):
        with pytest.raises(TemplateSyntaxInvalid) as caught:
            render_jinja("line1\n{% if x %}\nline3", {"x": True})
        assert caught.value.line is not None


class TestJinjaSandbox:
    """模板可能来自数据库（站点级/账号级设置），所以沙箱约束是安全边界，不是性能优化。"""

    def test_dunder_attribute_access_blocked(self):
        with pytest.raises(Exception):  # noqa: B017, PT011 — 具体类型由 Jinja 内部决定
            render_jinja("{{ ''.__class__.__mro__ }}", {})

    def test_import_not_accessible(self):
        with pytest.raises(MissingValue):
            render_jinja("{{ __import__('os') }}", {})

    def test_include_fails_no_loader(self):
        with pytest.raises(Exception):  # noqa: B017, PT011
            render_jinja("{% include 'other.html' %}", {})

    def test_extends_fails_no_loader(self):
        with pytest.raises(Exception):  # noqa: B017, PT011
            render_jinja("{% extends 'base.html' %}", {})

    def test_autoescape_is_off(self):
        """输出是纯文本给 LLM，不是 HTML；转义是调用方的事。"""
        assert render_jinja("{{ x }}", {"x": "<b>bold</b>"}) == "<b>bold</b>"


class TestJinjaOutputLimit:
    def test_within_limit_passes(self):
        assert render_jinja("{{ x }}", {"x": "short"}, max_output_chars=100) == "short"

    def test_over_limit_raises(self):
        with pytest.raises(OutputTooLarge) as caught:
            render_jinja("{{ x * 10 }}", {"x": "a" * 20}, max_output_chars=50)
        assert caught.value.limit == 50

    def test_runaway_loop_caught_by_limit(self):
        """Jinja 没有超时机制——输出长度上限是刹车之一；沙箱自身的
        safe_range() 对 range() 大小另有一道防线（下一条测试）。"""
        template = "{% for i in range(99999) %}x{% endfor %}"
        with pytest.raises(OutputTooLarge):
            render_jinja(template, {}, max_output_chars=1000)

    def test_oversized_range_rejected_by_sandbox(self):
        """沙箱的 safe_range() 保护抛的是裸 OverflowError，不是 jinja2 的
        异常类型——会直接穿透普通的 except 边界。必须在本模块里收口，
        否则调用方捕 TemplateError 捕不住它（这是单测跑出来的真 bug）。"""
        template = "{% for i in range(10**9) %}x{% endfor %}"
        with pytest.raises(TemplateError):
            render_jinja(template, {}, max_output_chars=10**7)

    def test_nonpositive_limit_rejected(self):
        with pytest.raises(ValueError):
            render_jinja("{{ x }}", {"x": "a"}, max_output_chars=0)


class TestJinjaVariables:
    def test_lists_top_level_variables(self):
        assert jinja_variables("{{ title }} by {{ author }}") == ["author", "title"]

    def test_dedupes(self):
        assert jinja_variables("{{ title }} {{ title }}") == ["title"]

    def test_loop_variable_not_listed_as_undeclared(self):
        """for 循环引入的变量 a 不该被当成"需要调用方提供"的变量。"""
        assert jinja_variables("{% for a in authors %}{{ a }}{% endfor %}") == ["authors"]

    def test_no_variables(self):
        assert jinja_variables("plain text") == []

    def test_syntax_error_raises(self):
        with pytest.raises(TemplateSyntaxInvalid):
            jinja_variables("{% if x %}")
