from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
ADDON_DIR = ROOT / "addon"
DIST_DIR = ROOT / "dist"
OUTPUT_FILE = DIST_DIR / "share_tools.ankiaddon"

EXCLUDED_NAMES = {
    "__pycache__",
    ".DS_Store",
    "unsuspend_tracker_state.json",
}

EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
}

REQUIRED_NAMES = {
    "manifest.json",
    "__init__.py",
    "share_tools/__init__.py",
    "share_tools/browser_actions.py",
    "share_tools/browser_widget.py",
    "share_tools/queries.py",
    "share_tools/unsuspend_tracker.py",
}


def should_include(path: Path) -> bool:
    if any(part in EXCLUDED_NAMES for part in path.parts):
        return False

    if path.suffix in EXCLUDED_SUFFIXES:
        return False

    return True


def main() -> None:
    if not (ADDON_DIR / "manifest.json").exists():
        raise FileNotFoundError("Missing addon/manifest.json")

    if not (ADDON_DIR / "__init__.py").exists():
        raise FileNotFoundError("Missing addon/__init__.py")

    DIST_DIR.mkdir(exist_ok=True)

    if OUTPUT_FILE.exists():
        OUTPUT_FILE.unlink()

    with ZipFile(OUTPUT_FILE, "w", compression=ZIP_DEFLATED) as zf:
        for path in ADDON_DIR.rglob("*"):
            if not path.is_file():
                continue

            if not should_include(path):
                continue

            arcname = path.relative_to(ADDON_DIR)
            zf.write(path, arcname)

    with ZipFile(OUTPUT_FILE) as zf:
        names = set(zf.namelist())

    missing_names = REQUIRED_NAMES - names

    if missing_names:
        missing = ", ".join(sorted(missing_names))
        raise RuntimeError(f"Built archive is missing required files: {missing}")

    if "addon/manifest.json" in names:
        raise RuntimeError("Built archive incorrectly contains addon/manifest.json")

    print(f"Built {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
