"""Global configuration and defaults."""

from pathlib import Path

# Default paths
DEFAULT_DB_PATH = Path("schnabel.db")
DEFAULT_DATA_DIR = Path("data/input")
DEFAULT_OUTPUT_DIR = Path("output")

# Phone number defaults
DEFAULT_PHONE_REGION = "CH"

# Photo normalization defaults
PHOTO_MAX_SIZE = 400
PHOTO_JPEG_QUALITY = 85

# Encoding fallback chain for vCard files
ENCODING_CHAIN = ["utf-8", "utf-8-sig", "iso-8859-1", "cp1252", "utf-16"]

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

# Matching weights
WEIGHT_EMAIL = 0.40
WEIGHT_PHONE = 0.35
WEIGHT_NAME = 0.15
WEIGHT_PHOTO = 0.08
WEIGHT_ADDRESS = 0.02

# Anchor rules
ANCHOR_MIN_SHARED_EMAIL_OR_PHONE = 0.70
ANCHOR_MIN_SHARED_EMAIL_AND_PHONE = 0.95
ANCHOR_MIN_SHARED_CONTACT_AND_NAME = 0.85  # shared email/phone + high name match
ANCHOR_MAX_NAME_ONLY = 0.60
