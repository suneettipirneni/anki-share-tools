import pytest

from anki.collection import OpChanges
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
        self.suspend_calls: list[list[int]] = []
        self.unsuspend_calls: list[list[int]] = []

    def suspend_cards(self, card_ids: list[int]) -> None:
        self.suspend_calls.append(list(card_ids))
        for card_id in card_ids:
            self.col.cards[card_id].queue = -1

    def unsuspend_cards(self, card_ids: list[int]) -> None:
        self.unsuspend_calls.append(list(card_ids))
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
        self.undo_entries: list[str] = []
        self.merged_undo_entries: list[int] = []

    def get_card(self, card_id: int) -> FakeCard:
        return self.cards[card_id]

    def get_note(self, note_id: int) -> FakeNote:
        return self.notes[note_id]

    def add_custom_undo_entry(self, name: str) -> int:
        self.undo_entries.append(name)
        return 42

    def merge_undo_entries(self, target: int) -> OpChanges:
        self.merged_undo_entries.append(target)
        return OpChanges(card=True, study_queues=True)


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

    operation_result = apply_patch_to_collection(col, patch)
    results = list(operation_result.results)

    assert col.cards[100].queue == 2
    assert col.cards[101].queue == 2
    assert operation_result.changes.card
    assert operation_result.changes.study_queues
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
    assert col.sched.unsuspend_calls == [[100]]
    assert col.sched.suspend_calls == []
    assert col.undo_entries == ["Apply ankipatch"]
    assert col.merged_undo_entries == [42]


def test_apply_patch_batches_suspend_and_unsuspend_into_one_undo_entry() -> None:
    col = FakeCollection()
    patch = AnkiPatch(
        cards=[
            CardPatchRow("guid-a", 0, False),
            CardPatchRow("guid-a", 1, True),
        ]
    )

    operation_result = apply_patch_to_collection(col, patch)

    assert [result.status for result in operation_result.results] == [
        "updated",
        "updated",
    ]
    assert col.sched.suspend_calls == [[101]]
    assert col.sched.unsuspend_calls == [[100]]
    assert col.undo_entries == ["Apply ankipatch"]
    assert col.merged_undo_entries == [42]


def test_apply_patch_rechecks_selected_rows_and_skips_empty_undo() -> None:
    col = FakeCollection()
    patch = AnkiPatch(
        cards=[
            CardPatchRow("guid-a", 1, False),
            CardPatchRow("guid-missing", 0, True),
        ]
    )

    operation_result = apply_patch_to_collection(col, patch)

    assert [result.status for result in operation_result.results] == [
        "unchanged",
        "missing",
    ]
    assert not operation_result.changes.card
    assert not operation_result.changes.study_queues
    assert col.sched.suspend_calls == []
    assert col.sched.unsuspend_calls == []
    assert col.undo_entries == []
    assert col.merged_undo_entries == []


def test_apply_patch_retains_resolution_error_while_batching_other_rows() -> None:
    col = FakeCollection()
    original_get_card = col.get_card

    def get_card(card_id: int) -> FakeCard:
        if card_id == 101:
            raise RuntimeError("card could not be loaded")
        return original_get_card(card_id)

    col.get_card = get_card  # type: ignore[method-assign]
    patch = AnkiPatch(
        cards=[
            CardPatchRow("guid-a", 0, False),
            CardPatchRow("guid-a", 1, True),
        ]
    )

    operation_result = apply_patch_to_collection(col, patch)

    assert [result.status for result in operation_result.results] == [
        "updated",
        "error",
    ]
    assert operation_result.results[1].message == "card could not be loaded"
    assert col.sched.unsuspend_calls == [[100]]
    assert col.sched.suspend_calls == []
    assert col.undo_entries == ["Apply ankipatch"]


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
    assert col.undo_entries == []


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
        ).results
    ]

    report = format_apply_report(results)

    assert "1 successful, 1 unsuccessful" in report
    assert "Successful" in report
    assert "Unsuccessful" in report
    assert "guid-missing" in report
