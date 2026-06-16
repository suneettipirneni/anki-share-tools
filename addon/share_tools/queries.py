from dataclasses import dataclass
from datetime import date
import re
from typing import Iterable


DEFAULT_SHARE_TAG_PREFIX = "share_unsuspended"


@dataclass(frozen=True)
class ShareTag:
    class_name: str
    created_on: date

    def to_anki_tag(self) -> str:
        safe_class_name = normalize_tag_part(self.class_name)
        safe_date = self.created_on.isoformat().replace("-", "_")
        return f"{DEFAULT_SHARE_TAG_PREFIX}::{safe_class_name}_{safe_date}"


def normalize_tag_part(value: str) -> str:
    """
    Normalize user-facing text into a reasonable Anki tag segment.

    Example:
        "Cardiology Block 1" -> "cardiology_block_1"
    """
    cleaned = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_]+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_")


def build_nid_query(note_ids: Iterable[int]) -> str:
    unique_note_ids = sorted(set(note_ids))

    if not unique_note_ids:
        raise ValueError("No note IDs provided.")

    return "(" + " OR ".join(f"nid:{nid}" for nid in unique_note_ids) + ")"


def build_cid_query(card_ids: Iterable[int]) -> str:
    unique_card_ids = sorted(set(card_ids))

    if not unique_card_ids:
        raise ValueError("No card IDs provided.")

    return "(" + " OR ".join(f"cid:{cid}" for cid in unique_card_ids) + ")"


def build_tag_query(tag: str) -> str:
    if not tag.strip():
        raise ValueError("Tag cannot be empty.")

    return f"tag:{tag}"


def build_unsuspended_tag_query(tag: str) -> str:
    return f"{build_tag_query(tag)} -is:suspended"


def infer_common_class_tags(
    note_tags: list[list[str]],
    class_tag_prefix: str,
) -> list[str]:
    """
    Given tags from multiple notes, return class tags that appear.

    This does not require the tag to be present on every selected note.
    It simply finds all class tags among the selected notes.
    """
    prefix = class_tag_prefix.strip()

    if not prefix:
        raise ValueError("Class tag prefix cannot be empty.")

    tags: set[str] = set()

    for tag_list in note_tags:
        for tag in tag_list:
            if tag.startswith(prefix):
                tags.add(tag)

    return sorted(tags)


def build_class_query(class_tags: list[str], exclude_suspended: bool = True) -> str:
    unique_tags = sorted(set(class_tags))

    if not unique_tags:
        raise ValueError("No class tags provided.")

    if len(unique_tags) == 1:
        query = f"tag:{unique_tags[0]}"
    else:
        query = "(" + " OR ".join(f"tag:{tag}" for tag in unique_tags) + ")"

    if exclude_suspended:
        query += " -is:suspended"

    return query
