"""Tests for shared UI helpers."""

from schnabel.ui_helpers import truncate, confidence_bar


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
