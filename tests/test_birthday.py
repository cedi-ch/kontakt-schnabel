"""Tests for birthday ICS generation."""

from datetime import date

from schnabel.birthday import (
    calculate_age, generate_ics, parse_bday,
    get_birthday_contacts, get_missing_birthday_contacts,
    ReminderConfig, _escape_ics, _fold_line,
)
from schnabel.model import Contact, ContactField


# -- parse_bday --

def test_parse_bday_full_date():
    assert parse_bday("1985-04-03") == (1985, 4, 3)


def test_parse_bday_no_year():
    assert parse_bday("--04-03") == (None, 4, 3)


def test_parse_bday_compact():
    assert parse_bday("19850403") == (1985, 4, 3)


def test_parse_bday_with_time():
    assert parse_bday("1985-04-03T00:00:00") == (1985, 4, 3)


def test_parse_bday_invalid():
    assert parse_bday("garbage") is None
    assert parse_bday("") is None


# -- age --

def test_age_calculation():
    assert calculate_age(1985, 2026) == 41


# -- ICS generation --

def test_generate_ics_basic():
    c = Contact(id=1, fn="Hans Müller", category="real")
    c.fields.append(ContactField("bday", "1985-04-03"))
    entries = [(c, 1985, 4, 3)]

    ics = generate_ics(entries, years=2, reminders=ReminderConfig(triggers=["PT6H"]))
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "Hans Müller" in ics
    assert ics.count("BEGIN:VEVENT") == 2


def test_generate_ics_no_birth_year():
    c = Contact(id=2, fn="Anna Schmidt", category="real")
    c.fields.append(ContactField("bday", "--04-03"))
    entries = [(c, None, 4, 3)]

    ics = generate_ics(entries, years=2)
    assert "Anna Schmidt" in ics
    # No age in parentheses
    assert "(" not in ics.split("SUMMARY")[1].split("\r\n")[0]


def test_generate_ics_uid_deterministic():
    c = Contact(id=42, fn="Test", category="real")
    entries = [(c, 1990, 3, 15)]

    ics1 = generate_ics(entries, years=1)
    ics2 = generate_ics(entries, years=1)
    assert ics1 == ics2

    year = date.today().year
    assert f"schnabel-bday-42-{year}@kontakt-schnabel" in ics1


def test_generate_ics_feb29():
    c = Contact(id=3, fn="Leap Baby", category="real")
    entries = [(c, 2000, 2, 29)]

    ics = generate_ics(entries, years=10)
    # Not all 10 years should have events (non-leap years skipped)
    event_count = ics.count("BEGIN:VEVENT")
    assert event_count < 10
    assert event_count > 0


def test_generate_ics_valarm():
    c = Contact(id=4, fn="Reminder Test", category="real")
    entries = [(c, 1990, 6, 15)]
    reminders = ReminderConfig(triggers=["-P1D", "-P7D"])

    ics = generate_ics(entries, years=1, reminders=reminders)
    assert ics.count("BEGIN:VALARM") == 2
    assert "TRIGGER:-P1D" in ics
    assert "TRIGGER:-P7D" in ics


def test_generate_ics_transparent():
    c = Contact(id=5, fn="Transparent", category="real")
    entries = [(c, 1990, 1, 1)]

    ics = generate_ics(entries, years=1)
    assert "TRANSP:TRANSPARENT" in ics


def test_generate_ics_allday_format():
    c = Contact(id=6, fn="Allday", category="real")
    entries = [(c, 1990, 12, 25)]

    ics = generate_ics(entries, years=1)
    year = date.today().year
    assert f"DTSTART;VALUE=DATE:{year}1225" in ics
    assert f"DTEND;VALUE=DATE:{year}1226" in ics


def test_generate_ics_no_reminders():
    c = Contact(id=7, fn="No Alarm", category="real")
    entries = [(c, 1990, 1, 1)]
    reminders = ReminderConfig(triggers=[])

    ics = generate_ics(entries, years=1, reminders=reminders)
    assert "VALARM" not in ics


def test_generate_ics_categories():
    c = Contact(id=8, fn="Cat Test", category="real")
    c.fields.append(ContactField("categories", "Familie"))
    c.fields.append(ContactField("categories", "Pfadi"))
    entries = [(c, 1990, 1, 1)]

    ics = generate_ics(entries, years=1)
    assert "CATEGORIES:" in ics


# -- escaping / folding --

def test_escape_ics():
    assert _escape_ics("Hans; Müller") == "Hans\\; Müller"
    assert _escape_ics("a,b") == "a\\,b"


def test_fold_line_short():
    line = "SUMMARY:Short"
    assert _fold_line(line) == line


def test_fold_line_long():
    line = "SUMMARY:" + "A" * 100
    folded = _fold_line(line)
    assert "\r\n " in folded


# -- contact filtering (DB integration) --

def test_get_birthday_contacts(tmp_db):
    c1 = Contact(fn="Has BDAY", category="real")
    c1.fields.append(ContactField("bday", "1990-04-03"))
    c1.fields.append(ContactField("categories", "Familie"))
    tmp_db.insert_contact(c1)

    c2 = Contact(fn="No BDAY", category="real")
    c2.fields.append(ContactField("categories", "Familie"))
    tmp_db.insert_contact(c2)

    c3 = Contact(fn="Wrong Cat", category="real")
    c3.fields.append(ContactField("bday", "1995-06-15"))
    c3.fields.append(ContactField("categories", "Arbeit"))
    tmp_db.insert_contact(c3)
    tmp_db.commit()

    entries = get_birthday_contacts(tmp_db, {"Familie"})
    assert len(entries) == 1
    assert entries[0][0].fn == "Has BDAY"


def test_get_birthday_contacts_all(tmp_db):
    c1 = Contact(fn="Uncategorized", category="real")
    c1.fields.append(ContactField("bday", "1990-01-01"))
    tmp_db.insert_contact(c1)
    tmp_db.commit()

    entries = get_birthday_contacts(tmp_db, set(), include_all=True)
    assert len(entries) == 1


def test_get_missing_contacts(tmp_db):
    c1 = Contact(fn="Has BDAY", category="real")
    c1.fields.append(ContactField("bday", "1990-04-03"))
    tmp_db.insert_contact(c1)

    c2 = Contact(fn="No BDAY", category="real")
    tmp_db.insert_contact(c2)
    tmp_db.commit()

    missing = get_missing_birthday_contacts(tmp_db, set(), include_all=True)
    assert len(missing) == 1
    assert missing[0].fn == "No BDAY"


def test_generate_ics_crlf():
    """ICS must use CRLF line endings."""
    c = Contact(id=10, fn="CRLF Test", category="real")
    entries = [(c, 1990, 1, 1)]
    ics = generate_ics(entries, years=1)
    assert "\r\n" in ics
    # No bare \n (every \n should be preceded by \r)
    lines = ics.split("\r\n")
    for line in lines:
        assert "\n" not in line
