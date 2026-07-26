# Compatibility

## Support contract

Share Tools supports Anki 2.1.50 and newer when Anki is running with Qt6.
Qt5 is not supported. The current automated target is Anki 25.09.4 (the
`anki` and `aqt` distributions normalize this as `25.9.4`).

Anki 2.1.50 embeds Python 3.9, so the floor job uses Python 3.9. Python is not
versioned independently for end users: the supported Python runtime is the one
embedded by each supported Anki release.

The distribution manifest declares `"min_point_version": 50`. In the 2.1.x
release family, Anki represents the minimum as the final component of the
version, so `50` means Anki 2.1.50. Anki's local archive installer extracts an
incompatible add-on and records its metadata, but `AddonManager.loadAddons()`
skips it. The floor therefore prevents older Anki releases from loading or
executing Share Tools; it does not promise that a local archive is rejected
before extraction.

## API floor evidence

The table inventories the Anki/AQT APIs used by the integrated add-on. Source
links point to official Anki release tags. “2.1.45 or earlier” means the API is
present in the earliest historical release inspected for this matrix; its exact
introduction was not established because it cannot raise the Qt6-driven floor.

| Runtime surface | Use in Share Tools | Earliest evidenced release | Floor effect |
| --- | --- | --- | --- |
| `gui_hooks.main_window_did_init`, `browser_will_show`, `browser_will_show_context_menu`, `operation_did_execute`, `profile_did_open`, and `profile_will_close` | Register menus and the Browser panel, refresh after card/study-queue operations, and activate/deactivate profile-owned tracker state | 2.1.45 or earlier; the [2.1.45 generated hook declarations](https://github.com/ankitects/anki/blob/2.1.45/qt/tools/genhooks_gui.py) and call sites in [`main.py`](https://github.com/ankitects/anki/blob/2.1.45/qt/aqt/main.py) and [`browser.py`](https://github.com/ankitects/anki/blob/2.1.45/qt/aqt/browser/browser.py) contain all six hooks | None above 2.1.45 |
| `OpChanges.card` and `OpChanges.study_queues` | Decide whether a completed collection operation can affect tracker state | 2.1.45 or earlier in the [2.1.45 collection protocol](https://github.com/ankitects/anki/blob/2.1.45/proto/anki/collection.proto) | None above 2.1.45 |
| `aqt.operations.CollectionOp`, including `success()`, `failure()`, and `run_in_background()` | Apply an ankipatch as a native background collection operation and publish its `OpChanges` | 2.1.45 or earlier in [2.1.45 `aqt.operations`](https://github.com/ankitects/anki/blob/2.1.45/qt/aqt/operations/__init__.py) | None above 2.1.45 |
| `Collection.add_custom_undo_entry()` and `merge_undo_entries()` | Group batched suspend/unsuspend changes into one native undo step | 2.1.45 or earlier in [2.1.45 `collection.py`](https://github.com/ankitects/anki/blob/2.1.45/pylib/anki/collection.py) | None above 2.1.45 |
| `Scheduler.suspend_cards(Sequence[CardId])` and `unsuspend_cards(Sequence[CardId])` | Apply each target state in one scheduler batch | 2.1.45 or earlier in [2.1.45 `scheduler/base.py`](https://github.com/ankitects/anki/blob/2.1.45/pylib/anki/scheduler/base.py) | None above 2.1.45 |
| `Browser.selectedCards()` and `Browser.current_search()` | Read selected cards and the Browser search; `current_search()` also has a form-field fallback | Present by 2.1.49 and at the selected floor in [2.1.50 `browser.py`](https://github.com/ankitects/anki/blob/2.1.50/qt/aqt/browser/browser.py); exact introduction not established | None above 2.1.50 |
| `Collection.find_cards()`, `get_card()`, `get_note()`, card templates, note GUIDs, and `anki.errors.NotFoundError` | Resolve tracker scope and portable patch rows | Present at the selected floor in the [2.1.50 Python collection API](https://github.com/ankitects/anki/tree/2.1.50/pylib/anki); exact introductions not established | None above 2.1.50 |
| Qt6 scoped enums such as `Qt.ItemFlag`, `Qt.CheckState`, `Qt.ItemDataRole`, `Qt.ContextMenuPolicy`, `Qt.DockWidgetArea`, `QDialog.DialogCode`, and widget-specific `SelectionMode`/`SelectionBehavior` | Build the Browser dock, tables, dialogs, menus, dates, and timers without Qt5 compatibility names | 2.1.50. Anki 2.1.49 source uses legacy enum names, while [2.1.50 AQT uses the scoped Qt6 forms](https://github.com/ankitects/anki/blob/2.1.50/qt/aqt/addons.py) and provides the [Qt6 import layer](https://github.com/ankitects/anki/blob/2.1.50/qt/aqt/qt/qt6.py) | **Sets the Anki floor to 2.1.50** |
| `aqt.qt.sip.isdeleted()` | Avoid rendering deleted Qt objects during deferred tracker refreshes | Present by 2.1.49 and exported from the [2.1.50 Qt6 layer](https://github.com/ankitects/anki/blob/2.1.50/qt/aqt/qt/qt6.py); exact introduction not established | None above 2.1.50 |
| Manifest `min_point_version` and compatibility loading | Mark the archive incompatible below the supported floor and skip its import | Present by 2.1.45; [2.1.50 `AddonManager`](https://github.com/ankitects/anki/blob/2.1.50/qt/aqt/addons.py) defines the numeric field, compares it with `point_version()`, and skips incompatible add-ons during loading | Encodes 2.1.50 as numeric `50` |
| Embedded Python runtime | Execute the add-on inside Anki | 2.1.50 requires Python 3.9 in [`aqt/__init__.py`](https://github.com/ankitects/anki/blob/2.1.50/qt/aqt/__init__.py), builds the Anki wheel as `cp39` in [`pylib/anki/BUILD.bazel`](https://github.com/ankitects/anki/blob/2.1.50/pylib/anki/BUILD.bazel), and bundles CPython 3.9.x in [`pyoxidizer.bzl`](https://github.com/ankitects/anki/blob/2.1.50/qt/bundle/pyoxidizer.bzl) | Derives the floor test runtime; it is not a separate promise |

Anki's official [outside-AnkiWeb packaging guide](https://addon-docs.ankiweb.net/sharing.html)
defines `manifest.json` as the distribution manifest. `meta.json` is
Anki-managed installed state and is not used as this project's compatibility
contract.

## Verification matrix

| Target | Automated verification | Not automated |
| --- | --- | --- |
| Floor: Anki/AQT 2.1.50 Qt6, Python 3.9 | Pyright and the full unit suite in a fresh isolated environment using exact `anki==2.1.50` and `aqt[qt6]==2.1.50` dependencies | Interactive behavior inside the packaged Anki application |
| Current: locked Anki/AQT 25.09.4, Python 3.13 | Ruff, Pyright, the full unit suite, package build, and manifest parsing through the installed Anki `AddonManager` | Interactive behavior inside the packaged Anki application |

The dependency lower bounds reproduce the floor, while `uv.lock` records the
current automated target. There is no arbitrary upper bound; adopting a newer
Anki API must update this matrix and raise the manifest and dependency floors
together.

## Manual smoke checklist

Run this checklist in both Anki 2.1.50 Qt6 and the current supported Anki before
a release when practical. Record the Anki version and platform with the release
test result; this document does not claim the checklist is automated.

1. Install the built `.ankiaddon`, restart Anki, and confirm it loads without an
   add-on error.
2. Open Browse and show the Share Tools panel.
3. Lock a search scope, unsuspend a card in that scope, and confirm the tracker
   captures it.
4. Filter the tracker with a preset and a custom inclusive date range.
5. Export selected/fresh cards to an `.ankipatch`.
6. Preview the full ledger, including `Will change`, `Same state`, missing, and
   error rows.
7. Apply selected changes and undo the single “Apply ankipatch” operation.
8. Switch profiles and confirm tracker state follows the active profile without
   leaking the previous profile's data.
