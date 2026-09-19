"""Verify that every relative Markdown link in the repository's documentation resolves.

Run from the repository root: ``uv run python scripts/check_doc_links.py``. Exit code 1 lists each
broken link as ``<file>: <target>``. External links (http, https, mailto) are not checked.
"""

import re
import sys
from pathlib import Path

LINK_PATTERN = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")
DOC_GLOBS = ("*.md", "docs/**/*.md", ".github/**/*.md", "infrastructure/**/*.md")
SKIPPED_FILES = frozenset({"SPECIFICATIONS.md"})


def markdown_files(root: Path) -> list[Path]:
    files = {path for pattern in DOC_GLOBS for path in root.glob(pattern)}
    return sorted(path for path in files if path.name not in SKIPPED_FILES)


def broken_links(markdown: Path) -> list[str]:
    text = markdown.read_text(encoding="utf-8")
    return [
        target
        for target in LINK_PATTERN.findall(text)
        if not target.startswith(EXTERNAL_PREFIXES) and not (markdown.parent / target).exists()
    ]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    failures = [
        f"{markdown.relative_to(root).as_posix()}: {target}"
        for markdown in markdown_files(root)
        for target in broken_links(markdown)
    ]
    if failures:
        sys.stdout.write("Broken documentation links:\n" + "\n".join(failures) + "\n")
        return 1
    sys.stdout.write("All relative documentation links resolve.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
