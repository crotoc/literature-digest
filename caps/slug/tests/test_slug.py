"""caps/slug 单测。不建库、不联网、不碰 infra。"""

import pytest

from caps.slug import normalize, slugify


class TestNormalize:
    def test_folds_whitespace(self):
        assert normalize("  hello \t\n world  ") == "hello world"

    def test_nfkc_fullwidth_to_ascii(self):
        assert normalize("ＡＢＣ１２３") == "ABC123"

    def test_nfkc_makes_equivalent_forms_equal(self):
        # "é" 的组合式与分解式
        assert normalize("café") == normalize("café")

    def test_empty(self):
        assert normalize("   ") == ""

    def test_idempotent(self):
        once = normalize("  Ａ  b\tc ")
        assert normalize(once) == once


class TestSlugifyBasic:
    def test_ascii(self):
        assert slugify("Hello, World!") == "hello_world"

    def test_casefold(self):
        assert slugify("HeLLo") == "hello"

    def test_collapses_runs_of_separators(self):
        assert slugify("a -- b ... c") == "a_b_c"

    def test_strips_leading_and_trailing(self):
        assert slugify("  ...a b...  ") == "a_b"

    def test_digits_kept(self):
        assert slugify("Nature 2019;567(7748):305") == "nature_2019_567_7748_305"

    def test_idempotent(self):
        for raw in ["Hello, World!", "Müller, K. (2019)", "深度学习", "!!!"]:
            once = slugify(raw)
            assert slugify(once) == once, raw


class TestSlugifyUnicode:
    def test_cjk_preserved(self):
        assert slugify("深度学习与蛋白质折叠") == "深度学习与蛋白质折叠"

    def test_cjk_with_punctuation(self):
        assert slugify("深度学习，蛋白质折叠") == "深度学习_蛋白质折叠"

    def test_latin_accents_preserved_not_transliterated(self):
        assert slugify("Müller, K. (2019)") == "müller_k_2019"

    def test_cyrillic_and_greek(self):
        assert slugify("Привет мир") == "привет_мир"
        assert slugify("Ωμέγα") == "ωμέγα"

    def test_mixed_scripts(self):
        assert slugify("CRISPR 基因编辑 2020") == "crispr_基因编辑_2020"


class TestSlugifyFallbackHash:
    def test_symbols_only_gets_hash(self):
        out = slugify("!!!")
        assert out.startswith("x")
        assert len(out) == 11
        assert out.isalnum()

    def test_emoji_only_gets_hash(self):
        out = slugify("🎉🎉")
        assert out.startswith("x")

    def test_empty_input_gets_hash(self):
        assert slugify("").startswith("x")

    def test_never_returns_empty(self):
        for raw in ["", "   ", "---", "...", "@#$%^&*", "🎉", "​"]:
            assert slugify(raw), f"{raw!r} 产生了空串"

    def test_hash_is_stable_across_calls(self):
        assert slugify("!!!") == slugify("!!!")

    def test_hash_differs_for_different_input(self):
        assert slugify("!!!") != slugify("???")

    def test_hash_ignores_whitespace_differences(self):
        # normalize 先折叠空白，所以这两个落同一个 hash
        assert slugify("!!!  ???") == slugify("!!! ???")


class TestSlugifySeparator:
    def test_dash(self):
        assert slugify("Hello, World!", separator="-") == "hello-world"

    def test_empty_separator(self):
        assert slugify("Hello, World!", separator="") == "helloworld"

    def test_dot(self):
        assert slugify("a b c", separator=".") == "a.b.c"

    def test_separator_with_alnum_rejected(self):
        with pytest.raises(ValueError):
            slugify("a b", separator="x")

    def test_separator_with_digit_rejected(self):
        with pytest.raises(ValueError):
            slugify("a b", separator="0")


class TestSlugifyMaxLength:
    def test_truncates(self):
        assert slugify("abcdefghij", max_length=4) == "abcd"

    def test_truncation_drops_trailing_separator(self):
        # "hello_world"[:6] == "hello_" → 去掉尾随下划线
        assert slugify("hello world", max_length=6) == "hello"

    def test_no_truncation_when_short_enough(self):
        assert slugify("ab", max_length=99) == "ab"

    def test_counts_characters_not_bytes(self):
        # 6 个汉字，max_length=3 → 留 3 个字符（不是 3 字节切坏 UTF-8）
        assert slugify("深度学习模型", max_length=3) == "深度学"

    def test_truncation_to_pure_separator_falls_back_to_hash(self):
        out = slugify("ab cd", max_length=2, separator="__")
        assert out.startswith("x") or out == "ab"

    def test_zero_rejected(self):
        with pytest.raises(ValueError):
            slugify("abc", max_length=0)

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            slugify("abc", max_length=-1)


class TestRealWorldTitles:
    """文献管理场景下的真实标题——给 attachments 命名模板用。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (
                "Attention Is All You Need",
                "attention_is_all_you_need",
            ),
            (
                "CRISPR-Cas9: a new era?",
                "crispr_cas9_a_new_era",
            ),
            (
                "A 10.1038/s41586-019-1234-5 DOI in a title",
                "a_10_1038_s41586_019_1234_5_doi_in_a_title",
            ),
            (
                "Smith et al. (2021), Cell 184(5)",
                "smith_et_al_2021_cell_184_5",
            ),
        ],
    )
    def test_titles(self, raw, expected):
        assert slugify(raw) == expected

    def test_long_title_truncated_cleanly(self):
        title = "Single-cell transcriptomic atlas of the human prefrontal cortex"
        out = slugify(title, max_length=40)
        assert len(out) <= 40
        assert not out.endswith("_")
        assert out == "single_cell_transcriptomic_atlas_of_the"
