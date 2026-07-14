import pytest

from share_tools.ankipatch import (
    AnkiPatch,
    CardPatchRow,
    apply_patch_to_collection,
    card_rows_from_card_ids,
    format_apply_report,
    parse_patch_text,
    preview_patch_against_collection,
    serialize_patch,
)


class FakeNote:
    def __init__(self, note_id: int, guid: str) -> None:
        self.id = note_id
        self.guid = guid


class FakeCard:
    def __init__(
        self,
        card_id: int,
        note_id: int,
        card_ord: int,
        queue: int,
        card_type: int = 2,
    ) -> None:
        self.id = card_id
        self.nid = note_id
        self.ord = card_ord
        self.queue = queue
        self.type = card_type


class FakeDb:
    def __init__(self, col: "FakeCollection") -> None:
        self.col = col

    def scalar(self, query: str, *args):
        if "SELECT c.id" in query:
            note_guid, card_ord = args
            for card in self.col.cards.values():
                note = self.col.notes[card.nid]
                if note.guid == note_guid and card.ord == card_ord:
                    return card.id
            return None

        if "SELECT guid FROM notes" in query:
            note_id = args[0]
            return self.col.notes[note_id].guid

        raise AssertionError(f"Unexpected query: {query}")


class FakeScheduler:
    def __init__(self, col: "FakeCollection") -> None:
        self.col = col

    def suspend_cards(self, card_ids: list[int]) -> None:
        for card_id in card_ids:
            self.col.cards[card_id].queue = -1

    def unsuspend_cards(self, card_ids: list[int]) -> None:
        for card_id in card_ids:
            card = self.col.cards[card_id]
            card.queue = card.type


class FakeCollection:
    def __init__(self) -> None:
        self.notes = {
            10: FakeNote(10, "guid-a"),
            20: FakeNote(20, "guid-b"),
        }
        self.cards = {
            100: FakeCard(100, 10, 0, -1),
            101: FakeCard(101, 10, 1, 2),
            200: FakeCard(200, 20, 0, 0, card_type=0),
        }
        self.db = FakeDb(self)
        self.sched = FakeScheduler(self)
        self.saved = False

    def get_card(self, card_id: int) -> FakeCard:
        return self.cards[card_id]

    def get_note(self, note_id: int) -> FakeNote:
        return self.notes[note_id]

    def save(self) -> None:
        self.saved = True


def test_serialize_and_parse_patch_round_trips_rows() -> None:
    patch = AnkiPatch(
        cards=[
            CardPatchRow("guid-b", 1, True),
            CardPatchRow("guid-a", 0, False),
        ],
        created_at="2026-06-23T12:00:00+00:00",
    )

    parsed = parse_patch_text(serialize_patch(patch))

    assert parsed == AnkiPatch(
        cards=[
            CardPatchRow("guid-a", 0, False),
            CardPatchRow("guid-b", 1, True),
        ],
        created_at="2026-06-23T12:00:00+00:00",
    )


def test_parse_rejects_conflicting_duplicate_rows() -> None:
    text = """
    {
      "format": "anki-share-tools/ankipatch",
      "version": 1,
      "cards": [
        {"note_guid": "guid-a", "card_ord": 0, "suspended": true},
        {"note_guid": "guid-a", "card_ord": 0, "suspended": false}
      ]
    }
    """

    with pytest.raises(ValueError, match="Conflicting ankipatch rows"):
        parse_patch_text(text)


def test_card_rows_from_card_ids_uses_note_guid_ord_and_suspended_state() -> None:
    col = FakeCollection()

    assert card_rows_from_card_ids(col, [101, 100, 100]) == [
        CardPatchRow("guid-a", 0, True),
        CardPatchRow("guid-a", 1, False),
    ]


def test_apply_patch_updates_by_note_guid_and_card_ord() -> None:
    col = FakeCollection()
    patch = AnkiPatch(
        cards=[
            CardPatchRow("guid-a", 0, False),
            CardPatchRow("guid-a", 1, False),
            CardPatchRow("guid-missing", 0, True),
        ]
    )

    results = apply_patch_to_collection(col, patch)

    assert col.cards[100].queue == 2
    assert col.cards[101].queue == 2
    assert col.saved
    assert [result.status for result in results] == [
        "updated",
        "unchanged",
        "missing",
    ]
    assert [(result.card_id, result.note_id) for result in results] == [
        (100, 10),
        (101, 10),
        (None, None),
    ]
    assert [result.previous_suspended for result in results] == [True, False, None]


def test_preview_patch_identifies_only_cards_that_need_changes() -> None:
    col = FakeCollection()
    patch = AnkiPatch(
        cards=[
            CardPatchRow("guid-a", 0, False),
            CardPatchRow("guid-a", 1, False),
            CardPatchRow("guid-missing", 0, True),
        ]
    )

    results = preview_patch_against_collection(col, patch)

    assert [result.status for result in results] == [
        "pending",
        "unchanged",
        "missing",
    ]
    assert [result.row for result in results if result.status == "pending"] == [
        CardPatchRow("guid-a", 0, False)
    ]
    assert col.cards[100].queue == -1
    assert not col.saved


def test_apply_report_splits_successful_and_unsuccessful_results() -> None:
    results = [
        *apply_patch_to_collection(
            FakeCollection(),
            AnkiPatch(
                cards=[
                    CardPatchRow("guid-a", 0, False),
                    CardPatchRow("guid-missing", 0, True),
                ]
            ),
        )
    ]

    report = format_apply_report(results)

    assert "1 successful, 1 unsuccessful" in report
    assert "Successful" in report
    assert "Unsuccessful" in report
    assert "guid-missing" in report
