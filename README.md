# Share Tools

An Anki add-on for exporting and applying portable `.ankipatch` files for suspended
and unsuspended card state.

https://github.com/user-attachments/assets/8cb20b97-80f0-43b8-bace-2808e15ac6c8

https://github.com/user-attachments/assets/6f5d154d-e3a1-412a-9070-a2b37e450d46


## Install

Install the add-on from the packaged `.ankiaddon` file:

1. Open the [GitHub Releases page](https://github.com/suneettipirneni/anki-share-tools/releases).
2. Open the latest release.
3. Download the `share_tools.ankiaddon` asset.
4. Open Anki.
5. Go to Tools -> Add-ons -> Install from file.
6. Select the downloaded `share_tools.ankiaddon` file.
7. Restart Anki.

## Compatibility

Share Tools supports Anki 2.1.50 and newer with Qt6. Qt5 builds are not
supported. The current automated target is Anki 25.09.4.

The minimum release uses Anki's embedded Python 3.9; Python is not a separate
end-user compatibility promise. CI type-checks and runs the full unit suite
against Anki/AQT 2.1.50 with Qt6 on Python 3.9, and lints, type-checks, tests,
and builds against the locked Anki/AQT 25.09.4 target on Python 3.13.

The packaged manifest declares the 2.1.50 floor. An older Anki release may
unpack a locally installed archive, but Anki records it as incompatible and
does not load or execute it. See [the compatibility matrix](docs/compatibility.md)
for API evidence and the manual smoke checklist.

## What It Adds

After restarting Anki, Share Tools adds a Browser panel for passive unsuspend tracking
and a context menu when you right-click selected cards.

The Browser panel lets you:

- Lock the current search as the tracking scope.
- Passively capture cards that become unsuspended while Anki is open.
- Keep captured unsuspensions and the suspended-card baseline in an add-on-owned
  SQLite database across Anki restarts.
- Choose a retention period of 1 day, 1 week, 1 month (the default), 1 year, or
  forever; expired entries are removed automatically.
- Filter captured unsuspensions with Today and This week shortcuts or an
  inclusive custom date range.
- Export fresh unsuspensions as a portable `.ankipatch`.
- Review the full patch ledger, choose which changes to apply, and inspect
  per-card results.

It exposes these actions under the Share Tools menu:

- Show fresh cards panel: reopens the Browser side panel if it was hidden.
- Export selected cards as ankipatch: saves selected cards with portable note GUID, card ordinal, and suspended/unsuspended state.
- Export current unsuspended class subset as ankipatch: infers the selected `class::` tag and exports matching unsuspended cards.
- Apply ankipatch: previews every patch row. Changes are checked and labeled
  `Will change`; matching cards remain visible but disabled as `Same state`;
  unresolved or errored rows are visible but cannot be selected. Only selected
  pending changes are applied, and final results include applied and unresolved
  rows.

You can also apply an `.ankipatch` from Anki's Tools menu.

## Development

This repo keeps development tooling at the repo root and the Anki add-on runtime under `addon/`.
Anki loads the add-on from `addon/`, while uv tooling runs from the repo root.

For local development, symlink the `addon/` folder into Anki's add-ons directory:

```sh
ln -s ~/code/anki-share-tools/addon ~/Library/Application\ Support/Anki2/addons21/share_tools
```

Run checks and build the distributable package from the repo root:

```sh
uv sync --locked
uv run ruff check .
uv run pyright
uv run pytest -q
uv run python scripts/build_ankiaddon.py
```

The build writes `dist/share_tools.ankiaddon`. Only the contents of `addon/` are packaged, so
`manifest.json` and `__init__.py` appear at the archive root.
