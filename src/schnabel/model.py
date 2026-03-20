"""Contact data model — pure data holder, no logic."""

from dataclasses import dataclass, field


@dataclass
class ContactField:
    """A single field of a contact (email, phone, address, etc.)."""
    field_type: str
    field_value: str
    field_params: dict = field(default_factory=dict)
    id: int | None = None


@dataclass
class Photo:
    """A contact photo with metadata."""
    photo_data: bytes
    photo_format: str  # JPEG, PNG, etc.
    byte_hash: str = ""
    perceptual_hash: str = ""
    width: int = 0
    height: int = 0


@dataclass
class Contact:
    """A parsed vCard contact."""
    id: int | None = None
    category: str = "unknown"  # real / stub / spam / unknown / deleted
    fn: str = ""
    family_name: str = ""
    given_name: str = ""
    additional_names: str = ""
    prefix: str = ""
    suffix: str = ""
    uid: str = ""
    source_file: str = ""
    source_import_id: int | None = None
    raw_vcard: str = ""
    merged_into_id: int | None = None
    is_active: bool = True

    fields: list[ContactField] = field(default_factory=list)
    photos: list[Photo] = field(default_factory=list)

    @property
    def emails(self) -> list[str]:
        return [f.field_value for f in self.fields if f.field_type == "email"]

    @property
    def phones(self) -> list[str]:
        return [f.field_value for f in self.fields if f.field_type == "tel"]

    @property
    def addresses(self) -> list[str]:
        return [f.field_value for f in self.fields if f.field_type == "adr"]

    @property
    def orgs(self) -> list[str]:
        return [f.field_value for f in self.fields if f.field_type == "org"]

    @property
    def categories(self) -> list[str]:
        return [f.field_value for f in self.fields if f.field_type == "categories"]

    @property
    def has_structured_name(self) -> bool:
        """True if the contact has a real name (not just N:;;;;)."""
        return bool(self.family_name.strip() or self.given_name.strip())

    @property
    def field_count(self) -> int:
        """Number of meaningful fields (for richness comparison)."""
        count = 0
        if self.has_structured_name:
            count += 1
        count += len(self.emails)
        count += len(self.phones)
        count += len(self.addresses)
        count += len(self.orgs)
        count += len(self.photos)
        return count
