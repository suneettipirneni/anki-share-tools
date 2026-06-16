# Share Tools

An Anki add-on for copying shareable browser queries and tagging unsuspended class subsets.

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

## What It Adds

After restarting Anki, Share Tools adds a Browser panel for passive unsuspend tracking
and a context menu when you right-click selected cards.

The Browser panel lets you:

- Lock the current search as the tracking scope.
- Passively capture cards that become unsuspended while Anki is open.
- Keep captured unsuspensions across Anki restarts.
- Switch between Today and This week freshness windows.
- Copy an exact `cid:` query for freshly unsuspended cards.
- Optionally tag parent notes for friend-facing sharing.

It exposes these actions under the Share Tools menu:

- Copy exact selected notes query: copies an Anki search query for the selected notes by note ID.
- Copy class query excluding suspended: infers selected `class::` tags and copies a query for those class cards while excluding suspended cards.
- Tag current unsuspended class subset: tags the current unsuspended cards for a selected class with a temporary `share_unsuspended::...` tag, then copies a query for that share tag.

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
uv run python scripts/build_ankiaddon.py
```

The build writes `dist/share_tools.ankiaddon`. Only the contents of `addon/` are packaged, so
`manifest.json` and `__init__.py` appear at the archive root.
