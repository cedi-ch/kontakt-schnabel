"""Tests for shared UI helpers."""

from schnabel.ui_helpers import truncate, confidence_bar, key_to_field_index, index_to_label


def test_truncate_short():
    assert truncate("hello", 10) == "hello"


def test_truncate_exact():
    assert truncate("hello", 5) == "hello"


def test_truncate_long():
    result = truncate("hello world", 8)
    assert len(result) == 8
    assert result.endswith("\u2026")


def test_truncate_default_maxlen():
    short = "short"
    assert truncate(short) == short
    long = "x" * 50
    result = truncate(long)
    assert len(result) == 30


def test_confidence_bar_high():
    bar = confidence_bar(0.95)
    text = bar.plain
    assert "95%" in text


def test_confidence_bar_low():
    bar = confidence_bar(0.30)
    text = bar.plain
    assert "30%" in text


def test_confidence_bar_zero():
    bar = confidence_bar(0.0)
    text = bar.plain
    assert "0%" in text


# --- Field index mapping (0-9, a-z) ---

def test_key_to_field_index_digits():
    assert key_to_field_index("0", 9) == 0
    assert key_to_field_index("9", 9) == 9
    assert key_to_field_index("5", 20) == 5


def test_key_to_field_index_letters():
    assert key_to_field_index("a", 15) == 10
    assert key_to_field_index("b", 15) == 11
    assert key_to_field_index("f", 15) == 15


def test_key_to_field_index_out_of_range():
    assert key_to_field_index("a", 9) is None  # 10 > max 9
    assert key_to_field_index("z", 5) is None


def test_key_to_field_index_invalid():
    assert key_to_field_index("!", 20) is None
    assert key_to_field_index(" ", 20) is None


def test_index_to_label():
    assert index_to_label(0) == "0"
    assert index_to_label(9) == "9"
    assert index_to_label(10) == "a"
    assert index_to_label(15) == "f"
    assert index_to_label(35) == "z"
