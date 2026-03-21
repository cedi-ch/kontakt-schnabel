"""Tests for normalization pipeline."""

from schnabel.normalize import (
    normalize_email,
    normalize_phone,
    name_key,
    name_simplified,
    fn_simplified,
)
from schnabel.model import Contact


def test_normalize_email():
    assert normalize_email("Hans@Gmail.com") == "hans@gmail.com"
    assert normalize_email("test@googlemail.com") == "test@gmail.com"
    assert normalize_email("  bob@test.ch  ") == "bob@test.ch"


def test_normalize_phone():
    assert normalize_phone("079 123 45 67") == "+41791234567"
    assert normalize_phone("+41 79 123 45 67") == "+41791234567"
    assert normalize_phone("0791234567") == "+41791234567"
    # Invalid number
    assert normalize_phone("123") is None


def test_name_key():
    c = Contact(given_name="Hans", family_name="Müller")
    assert name_key(c) == "hans müller"


def test_name_simplified():
    c = Contact(given_name="Hans", family_name="Müller")
    assert name_simplified(c) == "hans muller"


def test_name_simplified_reorder():
    """Token-sorted: order doesn't matter."""
    c1 = Contact(given_name="Hans", family_name="Müller")
    c2 = Contact(given_name="Müller", family_name="Hans")
    assert name_simplified(c1) == name_simplified(c2)


def test_fn_simplified():
    assert fn_simplified("Müller, Hans") == "hans muller"
    assert fn_simplified("  Hans  Müller  ") == "hans muller"


def test_normalize_bday_iso():
    from schnabel.normalize import normalize_bday
    assert normalize_bday("1990-04-29") == "1990-04-29"


def test_normalize_bday_compact():
    from schnabel.normalize import normalize_bday
    assert normalize_bday("19900429") == "1990-04-29"


def test_normalize_bday_with_time():
    from schnabel.normalize import normalize_bday
    assert normalize_bday("1990-04-29T00:00:00") == "1990-04-29"


def test_normalize_bday_invalid():
    from schnabel.normalize import normalize_bday
    assert normalize_bday("not-a-date") is None
    assert normalize_bday("") is None
