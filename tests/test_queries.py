from datetime import date

import pytest

from share_tools.queries import (
    ShareTag,
    build_class_query,
    build_nid_query,
    build_unsuspended_tag_query,
    infer_common_class_tags,
    normalize_tag_part,
)


def test_build_nid_query_deduplicates_and_sorts() -> None:
    assert build_nid_query([3, 1, 3, 2]) == "(nid:1 OR nid:2 OR nid:3)"


def test_build_class_query_excludes_suspended_by_default() -> None:
    assert (
        build_class_query(["class::cardiology", "class::renal"])
        == "(tag:class::cardiology OR tag:class::renal) -is:suspended"
    )


def test_build_unsuspended_tag_query() -> None:
    assert build_unsuspended_tag_query("share_unsuspended::cardiology") == (
        "tag:share_unsuspended::cardiology -is:suspended"
    )


def test_infer_common_class_tags() -> None:
    assert infer_common_class_tags(
        note_tags=[
            ["class::cardiology", "deck::step1"],
            ["class::renal", "class::cardiology"],
        ],
        class_tag_prefix="class::",
    ) == ["class::cardiology", "class::renal"]


def test_share_tag_normalizes_parts() -> None:
    assert normalize_tag_part("Cardiology Block 1") == "cardiology_block_1"
    assert (
        ShareTag(
            class_name="Cardiology Block 1",
            created_on=date(2026, 6, 9),
        ).to_anki_tag()
        == "share_unsuspended::cardiology_block_1_2026_06_09"
    )


def test_empty_nid_query_raises() -> None:
    with pytest.raises(ValueError, match="No note IDs provided"):
        build_nid_query([])
