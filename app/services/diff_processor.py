import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("prlens")


# ──────────────────────────────────────────────────────────
#  File Filters — skip files that waste LLM tokens
# ──────────────────────────────────────────────────────────

SKIP_EXTENSIONS = {
    # Lock / generated
    ".lock",
    # Minified / bundled
    ".min.js", ".min.css", ".bundle.js", ".chunk.js",
    # Source maps
    ".map",
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp", ".avif",
    # Fonts
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    # Binary / compiled
    ".pyc", ".pyo", ".class", ".o", ".so", ".dll", ".exe", ".bin", ".wasm",
    # Data
    ".csv", ".parquet", ".sqlite", ".db",
    # Docs
    ".pdf", ".doc", ".docx",
}

SKIP_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Pipfile.lock",
    "poetry.lock",
    "composer.lock",
    "Gemfile.lock",
    "Cargo.lock",
    "go.sum",
    "flake.lock",
    ".DS_Store",
    "Thumbs.db",
}

SKIP_PATH_SEGMENTS = {
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    ".next/",
    "__pycache__/",
    ".git/",
}


# ──────────────────────────────────────────────────────────
#  Data Structures
# ──────────────────────────────────────────────────────────

@dataclass
class DiffHunk:
    """A single @@ hunk within a file diff."""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    """Parsed diff for a single file."""
    path: str
    raw_diff: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False
    is_renamed: bool = False

    @property
    def added_lines(self) -> set[int]:
        """Line numbers (new file) that were added (+)."""
        result = set()
        for hunk in self.hunks:
            line_num = hunk.new_start
            for line in hunk.lines:
                if line.startswith("+"):
                    result.add(line_num)
                    line_num += 1
                elif line.startswith("-"):
                    pass  # deletions don't move the new-file counter
                else:
                    line_num += 1
        return result

    @property
    def reviewable_lines(self) -> set[int]:
        """All new-file line numbers visible in the diff (for inline comments).

        Includes both added lines (+) and context lines ( ). These are the
        only lines GitHub allows inline comments on.
        """
        result = set()
        for hunk in self.hunks:
            line_num = hunk.new_start
            for line in hunk.lines:
                if line.startswith("-"):
                    pass
                else:
                    result.add(line_num)
                    line_num += 1
        return result


# ──────────────────────────────────────────────────────────
#  Filtering
# ──────────────────────────────────────────────────────────

def should_skip_file(path: str) -> bool:
    """Return True if the file should be excluded from review."""
    filename = path.split("/")[-1]

    if filename in SKIP_FILENAMES:
        return True

    for ext in SKIP_EXTENSIONS:
        if path.endswith(ext):
            return True

    for segment in SKIP_PATH_SEGMENTS:
        if segment in path:
            return True

    return False


# ──────────────────────────────────────────────────────────
#  Diff Parsing
# ──────────────────────────────────────────────────────────

HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(raw_diff: str) -> list[FileDiff]:
    """Parse a unified diff string into structured FileDiff objects."""
    file_diffs: list[FileDiff] = []
    current_file: FileDiff | None = None
    current_hunk: DiffHunk | None = None

    for line in raw_diff.splitlines():
        # ── New file header ──
        if line.startswith("diff --git"):
            if current_file:
                file_diffs.append(current_file)

            match = re.search(r"diff --git a/(.*) b/(.*)", line)
            path = match.group(2) if match else "unknown"
            current_file = FileDiff(path=path, raw_diff=line + "\n")
            current_hunk = None
            continue

        if current_file is None:
            continue

        current_file.raw_diff += line + "\n"

        # ── File metadata ──
        if line.startswith("new file"):
            current_file.is_new = True
        elif line.startswith("deleted file"):
            current_file.is_deleted = True
        elif line.startswith("rename from") or line.startswith("rename to"):
            current_file.is_renamed = True

        # ── Hunk header ──
        hunk_match = HUNK_HEADER_RE.match(line)
        if hunk_match:
            current_hunk = DiffHunk(
                old_start=int(hunk_match.group(1)),
                old_count=int(hunk_match.group(2) or 1),
                new_start=int(hunk_match.group(3)),
                new_count=int(hunk_match.group(4) or 1),
            )
            current_file.hunks.append(current_hunk)
            continue

        # ── Diff content ──
        if current_hunk is not None and len(line) > 0:
            if line[0] in ("+", "-", " "):
                current_hunk.lines.append(line)

    if current_file:
        file_diffs.append(current_file)

    return file_diffs


def filter_diffs(file_diffs: list[FileDiff], max_files: int = 30) -> list[FileDiff]:
    """Filter out non-reviewable files (binaries, lock files, deletions)."""
    filtered = []
    skipped = []

    for fd in file_diffs:
        if should_skip_file(fd.path):
            skipped.append(fd.path)
            continue
        if fd.is_deleted:
            skipped.append(f"{fd.path} (deleted)")
            continue
        filtered.append(fd)

    if skipped:
        logger.info(f"Skipped {len(skipped)} files: {', '.join(skipped[:15])}")

    if len(filtered) > max_files:
        logger.warning(f"PR has {len(filtered)} reviewable files, capping at {max_files}")
        filtered = filtered[:max_files]

    return filtered
