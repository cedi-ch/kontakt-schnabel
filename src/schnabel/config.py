"""Global configuration and defaults."""

from datetime import datetime
from pathlib import Path

# Default paths
DEFAULT_DB_PATH = Path("schnabel.db")
DEFAULT_DATA_DIR = Path("data/input")
DEFAULT_OUTPUT_DIR = Path("output")


def make_output_dir(command_name: str, base_dir: Path | None = None) -> Path:
    """Create a timestamped output directory.

    Format: output/2026-02-22_2149_command/
    Returns the created Path.
    """
    base = base_dir or DEFAULT_OUTPUT_DIR
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    dirname = f"{timestamp}_{command_name}"
    output_dir = base / dirname
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir

# Phone number defaults
DEFAULT_PHONE_REGION = "CH"

# Photo normalization defaults
PHOTO_MAX_SIZE = 400
PHOTO_JPEG_QUALITY = 85

# Encoding fallback chain for vCard files
ENCODING_CHAIN = ["utf-8", "utf-8-sig", "utf-16", "iso-8859-1", "cp1252"]

# Spam indicators in email addresses
SPAM_LOCAL_PARTS = {
    "noreply", "no-reply", "no_reply", "newsletter", "newsletters",
    "promo", "promotions", "marketing", "info", "sales",
    "abo", "unsubscribe", "mailer-daemon", "postmaster",
    "donotreply", "do-not-reply", "bounce", "notifications",
    "notify", "alert", "alerts", "news", "digest",
}

SPAM_DOMAINS = {
    "noreply.com", "example.com", "example.org",
}

# Matching weights (must sum to 1.0)
WEIGHT_EMAIL = 0.35
WEIGHT_PHONE = 0.30
WEIGHT_NAME = 0.13
WEIGHT_BDAY = 0.12
WEIGHT_PHOTO = 0.08
WEIGHT_ADDRESS = 0.02

# Anchor rules
ANCHOR_MIN_SHARED_EMAIL_OR_PHONE = 0.70
ANCHOR_MIN_SHARED_EMAIL_AND_PHONE = 0.95
ANCHOR_MIN_SHARED_CONTACT_AND_NAME = 0.85  # shared email/phone + high name match
ANCHOR_MIN_SHARED_BDAY_AND_NAME = 0.80     # shared birthday + similar name
ANCHOR_MAX_NAME_ONLY = 0.60
