import pytest

from domain.works.fingerprint import compute_title_year_key


def test_same_title_and_year_produce_same_key():
    a = compute_title_year_key("Deep Learning for Genomics", 2020)
    b = compute_title_year_key("deep learning for genomics", 2020)
    assert a == b


def test_different_year_produces_different_key():
    a = compute_title_year_key("Deep Learning for Genomics", 2020)
    b = compute_title_year_key("Deep Learning for Genomics", 2021)
    assert a != b


def test_punctuation_and_whitespace_are_normalized_away():
    a = compute_title_year_key("Deep Learning for Genomics!", 2020)
    b = compute_title_year_key("Deep   Learning for Genomics", 2020)
    assert a == b


def test_title_only_ok():
    key = compute_title_year_key("Some Title With No Year", None)
    assert key.endswith("_")


def test_year_only_ok():
    key = compute_title_year_key(None, 2020)
    assert key.startswith("_")


def test_both_missing_raises():
    with pytest.raises(ValueError):
        compute_title_year_key(None, None)
    with pytest.raises(ValueError):
        compute_title_year_key("   ", None)
