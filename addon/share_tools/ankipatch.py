from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from anki.collection import OpChanges


FORMAT_NAME = "anki-share-tools/ankipatch"
FORMAT_VERSION = 1
ANKIPATCH_SUFFIX = ".ankipatch"
SUSPENDED_QUEUE = -1
# These bounds keep a user-selected interchange file small enough to validate and
# review in memory without turning the Anki UI workflow into an unbounded workload.
MAX_ANKIPATCH_BYTES = 10 * 1024 * 1024
MAX_ANKIPATCH_ROWS = 50_000


@dataclass(frozen=True)
class CardPatchRow:
    note_guid: str
    card_ord: int
    suspended: bool

    def key(self) -> tuple[str, int]:
        return (self.note_guid, self.card_ord)


@dataclass(frozen=True)
class AnkiPatch:
    cards: list[CardPatchRow]
    created_at: Optional[str] = None


@dataclass(frozen=True)
class PatchApplyResult:
    row: CardPatchRow
    card_id: Optional[int]
    status: str
    message: str
    note_id: Optional[int] = None
    previous_suspended: Optional[bool] = None

    @property
    def successful(self) -> bool:
        return self.status in {"updated", "unchanged"}

    @property
    def resolved(self) -> bool:
        return self.card_id is not None


@dataclass(frozen=True)
class ResolvedPatchOperation:
    row: CardPatchRow
    card_id: int
    note_id: int
    previous_suspended: bool
    target_suspended: bool


@dataclass(frozen=True)
class PatchOperationResult:
    changes: OpChanges
    results: tuple[PatchApplyResult, ...]


def serialize_patch(patch: AnkiPatch) -> str:
    created_at = patch.created_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "created_at": created_at,
        "cards": [
            {
                "note_guid": row.note_guid,
                "card_ord": row.card_ord,
                "suspended": row.suspended,
            }
            for row in normalize_rows(patch.cards)
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def parse_patch_text(text: str) -> AnkiPatch:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid ankipatch JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Invalid ankipatch: expected a JSON object.")

    if payload.get("format") != FORMAT_NAME:
        raise ValueError(f"Invalid ankipatch format: expected {FORMAT_NAME}.")

    if payload.get("version") != FORMAT_VERSION:
        raise ValueError(f"Unsupported ankipatch version: {payload.get('version')!r}.")

    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        raise ValueError("Invalid ankipatch: cards must be a list.")
    if len(raw_cards) > MAX_ANKIPATCH_ROWS:
        raise ValueError(
            "Invalid ankipatch: cards exceed the "
            f"{MAX_ANKIPATCH_ROWS:,}-row limit "
            f"(observed {len(raw_cards):,} rows)."
        )

    rows = [parse_card_row(raw_card, index) for index, raw_card in enumerate(raw_cards)]
    return AnkiPatch(
        cards=normalize_rows(rows), created_at=optional_str(payload, "created_at")
    )


def read_patch(path: Path) -> AnkiPatch:
    file_size = path.stat().st_size
    enforce_patch_byte_limit(file_size)
    text = path.read_text(encoding="utf-8")
    enforce_patch_byte_limit(len(text.encode("utf-8")))
    return parse_patch_text(text)


def enforce_patch_byte_limit(observed_bytes: int) -> None:
    if observed_bytes > MAX_ANKIPATCH_BYTES:
        raise ValueError(
            "Ankipatch file exceeds the "
            f"{MAX_ANKIPATCH_BYTES:,}-byte limit "
            f"(observed {observed_bytes:,} bytes)."
        )


def write_patch(path: Path, patch: AnkiPatch) -> None:
    path.write_text(serialize_patch(patch), encoding="utf-8")


def card_rows_from_card_ids(col: Any, card_ids: Iterable[int]) -> list[CardPatchRow]:
    rows: list[CardPatchRow] = []

    for cid in sorted({int(card_id) for card_id in card_ids}):
        card = col.get_card(cid)
        note_guid = resolve_note_guid(col, int(card.nid))
        rows.append(
            CardPatchRow(
                note_guid=note_guid,
                card_ord=int(card.ord),
                suspended=int(card.queue) == SUSPENDED_QUEUE,
            )
        )

    return normalize_rows(rows)


def resolve_card_id(col: Any, row: CardPatchRow) -> Optional[int]:
    card_id = col.db.scalar(
        """
        SELECT c.id
        FROM cards c
        JOIN notes n ON n.id = c.nid
        WHERE n.guid = ?
          AND c.ord = ?
        """,
        row.note_guid,
        row.card_ord,
    )

    if card_id is None:
        return None

    return int(card_id)


def preview_patch_against_collection(
    col: Any,
    patch: AnkiPatch,
) -> list[PatchApplyResult]:
    results: list[PatchApplyResult] = []

    for row in patch.cards:
        card_id = resolve_card_id(col, row)

        if card_id is None:
            results.append(
                PatchApplyResult(
                    row=row,
                    card_id=None,
                    status="missing",
                    message="Card not found in this collection.",
                )
            )
            continue

        note_id: Optional[int] = None
        currently_suspended: Optional[bool] = None

        try:
            card = col.get_card(card_id)
            currently_suspended = int(card.queue) == SUSPENDED_QUEUE
            note_id = int(card.nid)

            if currently_suspended == row.suspended:
                status = "unchanged"
                message = "Already matched patch state."
            else:
                status = "pending"
                message = "Ready to apply."

            results.append(
                PatchApplyResult(
                    row=row,
                    card_id=card_id,
                    status=status,
                    message=message,
                    note_id=note_id,
                    previous_suspended=currently_suspended,
                )
            )
        except Exception as exc:
            results.append(
                PatchApplyResult(
                    row=row,
                    card_id=card_id,
                    status="error",
                    message=str(exc),
                    note_id=note_id,
                    previous_suspended=currently_suspended,
                )
            )

    return results


def apply_patch_to_collection(col: Any, patch: AnkiPatch) -> PatchOperationResult:
    prepared_rows: list[Union[ResolvedPatchOperation, PatchApplyResult]] = []

    for row in patch.cards:
        card_id = resolve_card_id(col, row)

        if card_id is None:
            prepared_rows.append(
                PatchApplyResult(
                    row=row,
                    card_id=None,
                    status="missing",
                    message="Card not found in this collection.",
                )
            )
            continue

        note_id: Optional[int] = None
        currently_suspended: Optional[bool] = None

        try:
            card = col.get_card(card_id)
            currently_suspended = int(card.queue) == SUSPENDED_QUEUE
            note_id = int(card.nid)

            if currently_suspended == row.suspended:
                prepared_rows.append(
                    PatchApplyResult(
                        row=row,
                        card_id=card_id,
                        status="unchanged",
                        message="Already matched patch state.",
                        note_id=note_id,
                        previous_suspended=currently_suspended,
                    )
                )
                continue

            prepared_rows.append(
                ResolvedPatchOperation(
                    row=row,
                    card_id=card_id,
                    note_id=note_id,
                    previous_suspended=currently_suspended,
                    target_suspended=row.suspended,
                )
            )
        except Exception as exc:
            prepared_rows.append(
                PatchApplyResult(
                    row=row,
                    card_id=card_id,
                    status="error",
                    message=str(exc),
                    note_id=note_id,
                    previous_suspended=currently_suspended,
                )
            )

    operations = [
        prepared
        for prepared in prepared_rows
        if isinstance(prepared, ResolvedPatchOperation)
    ]
    if not operations:
        return PatchOperationResult(
            changes=OpChanges(),
            results=tuple(
                prepared
                for prepared in prepared_rows
                if isinstance(prepared, PatchApplyResult)
            ),
        )

    undo_entry = col.add_custom_undo_entry("Apply ankipatch")
    suspend_card_ids = [
        operation.card_id for operation in operations if operation.target_suspended
    ]
    unsuspend_card_ids = [
        operation.card_id for operation in operations if not operation.target_suspended
    ]

    if suspend_card_ids:
        col.sched.suspend_cards(suspend_card_ids)
    if unsuspend_card_ids:
        col.sched.unsuspend_cards(unsuspend_card_ids)

    changes = col.merge_undo_entries(undo_entry)
    results = tuple(
        updated_patch_result(prepared)
        if isinstance(prepared, ResolvedPatchOperation)
        else prepared
        for prepared in prepared_rows
    )
    return PatchOperationResult(changes=changes, results=results)


def updated_patch_result(operation: ResolvedPatchOperation) -> PatchApplyResult:
    return PatchApplyResult(
        row=operation.row,
        card_id=operation.card_id,
        status="updated",
        message=(
            "Suspended card."
            if operation.target_suspended
            else "Unsuspended card."
        ),
        note_id=operation.note_id,
        previous_suspended=operation.previous_suspended,
    )


def format_apply_report(results: list[PatchApplyResult]) -> str:
    successful_count = sum(1 for result in results if result.successful)
    failed_count = len(results) - successful_count
    lines = [
        f"Applied ankipatch: {successful_count} successful, {failed_count} unsuccessful.",
        "",
        "Successful",
    ]

    successful = [result for result in results if result.successful]
    if successful:
        lines.extend(format_result_line(result) for result in successful)
    else:
        lines.append("None")

    lines.extend(["", "Unsuccessful"])
    unsuccessful = [result for result in results if not result.successful]
    if unsuccessful:
        lines.extend(format_result_line(result) for result in unsuccessful)
    else:
        lines.append("None")

    return "\n".join(lines)


def ensure_ankipatch_suffix(path: Path) -> Path:
    if path.suffix.lower() == ANKIPATCH_SUFFIX:
        return path

    return path.with_suffix(path.suffix + ANKIPATCH_SUFFIX)


def normalize_rows(rows: Iterable[CardPatchRow]) -> list[CardPatchRow]:
    by_key: dict[tuple[str, int], CardPatchRow] = {}

    for row in rows:
        key = row.key()
        existing = by_key.get(key)

        if existing is not None and existing.suspended != row.suspended:
            raise ValueError(
                "Conflicting ankipatch rows for "
                f"note_guid={row.note_guid!r}, card_ord={row.card_ord}."
            )

        by_key[key] = row

    return sorted(by_key.values(), key=lambda row: (row.note_guid, row.card_ord))


def parse_card_row(raw_card: Any, index: int) -> CardPatchRow:
    if not isinstance(raw_card, dict):
        raise ValueError(f"Invalid card row at index {index}: expected an object.")

    note_guid = raw_card.get("note_guid")
    card_ord = raw_card.get("card_ord")
    suspended = raw_card.get("suspended")

    if not isinstance(note_guid, str) or not note_guid.strip():
        raise ValueError(f"Invalid card row at index {index}: note_guid is required.")

    if not isinstance(card_ord, int) or isinstance(card_ord, bool) or card_ord < 0:
        raise ValueError(
            f"Invalid card row at index {index}: card_ord must be a non-negative integer."
        )

    if not isinstance(suspended, bool):
        raise ValueError(
            f"Invalid card row at index {index}: suspended must be true or false."
        )

    return CardPatchRow(
        note_guid=note_guid.strip(),
        card_ord=card_ord,
        suspended=suspended,
    )


def resolve_note_guid(col: Any, note_id: int) -> str:
    note = col.get_note(note_id)
    guid = getattr(note, "guid", "")

    if not guid:
        guid = col.db.scalar("SELECT guid FROM notes WHERE id = ?", note_id)

    if not guid:
        raise ValueError(f"Could not resolve note guid for note id {note_id}.")

    return str(guid)


def optional_str(payload: dict[str, Any], key: str) -> Optional[str]:
    value = payload.get(key)

    if value is None:
        return None

    if not isinstance(value, str):
        raise ValueError(f"Invalid ankipatch: {key} must be a string.")

    return value


def format_result_line(result: PatchApplyResult) -> str:
    card_id = result.card_id if result.card_id is not None else "not found"
    target = "suspended" if result.row.suspended else "unsuspended"
    return (
        f"- note_guid={result.row.note_guid}, card_ord={result.row.card_ord}, "
        f"card_id={card_id}, target={target}: {result.message}"
    )
