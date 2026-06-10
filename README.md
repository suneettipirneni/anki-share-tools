# Share Tools

An Anki add-on for copying shareable browser queries and tagging unsuspended class subsets.

## Install

1. Download `share_tools.ankiaddon` from the latest GitHub Release.
2. Open Anki.
3. Go to Tools -> Add-ons -> Install from file.
4. Select `share_tools.ankiaddon`.
5. Restart Anki.

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
