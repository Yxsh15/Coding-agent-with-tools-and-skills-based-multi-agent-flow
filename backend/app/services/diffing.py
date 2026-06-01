from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


@dataclass(frozen=True)
class DiffStats:
    path: str
    file_line_count: int
    hunk_count: int
    changed_line_count: int
    added_line_count: int
    removed_line_count: int
    context_line_count: int
    largest_hunk_change: int
    span_line_count: int
    change_ratio: float
    span_ratio: float


def _strip_diff_wrappers(diff_text: str) -> list[str]:
    cleaned: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("```"):
            continue
        if line.startswith(("diff --git ", "index ")):
            continue
        if line == r"\ No newline at end of file":
            continue
        cleaned.append(line)
    return cleaned


def _parse_unified_diff(diff_text: str) -> list[DiffHunk]:
    diff_lines = _strip_diff_wrappers(diff_text)
    hunks: list[DiffHunk] = []
    current_header: tuple[int, int, int, int] | None = None
    current_lines: list[str] = []

    for line in diff_lines:
        if line.startswith(("--- ", "+++ ")):
            continue
        if line.startswith("@@"):
            if current_header is not None:
                hunks.append(DiffHunk(*current_header, current_lines))
            match = HUNK_HEADER_RE.match(line)
            if not match:
                raise ValueError(f"Invalid unified diff hunk header: {line}")
            old_start = max(int(match.group(1) or "1") - 1, 0)
            old_count = int(match.group(2) or "1")
            new_start = max(int(match.group(3) or "1") - 1, 0)
            new_count = int(match.group(4) or "1")
            current_header = (old_start, old_count, new_start, new_count)
            current_lines = []
            continue
        if current_header is None:
            continue
        if line and line[0] not in {" ", "-", "+"}:
            raise ValueError(f"Unsupported diff line: {line}")
        current_lines.append(line)

    if current_header is not None:
        hunks.append(DiffHunk(*current_header, current_lines))

    if not hunks:
        raise ValueError("No hunks found in unified diff")
    return hunks


def _line_equals(left: str, right: str) -> bool:
    return left == right


def _normalized_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().replace("\r", ""))


def _normalized_line_equals(left: str, right: str) -> bool:
    return _normalized_line(left) == _normalized_line(right)


def _matches_hunk_at(
    original_lines: list[str],
    start_index: int,
    hunk_lines: list[str],
    comparator: Callable[[str, str], bool] = _line_equals,
) -> bool:
    if start_index < 0 or start_index > len(original_lines):
        return False
    source_index = start_index
    for hunk_line in hunk_lines:
        marker = hunk_line[:1]
        body = hunk_line[1:]
        if marker in {" ", "-"}:
            if source_index >= len(original_lines) or not comparator(original_lines[source_index], body):
                return False
            source_index += 1
    return True


def _context_blocks(hunk_lines: list[str]) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    current_block: list[str] = []
    block_offset: int | None = None
    source_offset = 0

    for hunk_line in hunk_lines:
        marker = hunk_line[:1]
        body = hunk_line[1:]
        if marker == " ":
            if block_offset is None:
                block_offset = source_offset
            current_block.append(body)
            source_offset += 1
            continue

        if current_block:
            blocks.append((block_offset or 0, current_block))
            current_block = []
            block_offset = None
        if marker == "-":
            source_offset += 1
        elif marker != "+":
            raise ValueError(f"Unsupported diff line: {hunk_line}")

    if current_block:
        blocks.append((block_offset or 0, current_block))

    return sorted(blocks, key=lambda item: len(item[1]), reverse=True)


def _block_positions(
    original_lines: list[str],
    block_lines: list[str],
    preferred_start: int,
    comparator: Callable[[str, str], bool],
) -> list[int]:
    if not block_lines:
        return []
    limit = len(original_lines) - len(block_lines) + 1
    positions = [
        index
        for index in range(max(limit, 0))
        if all(comparator(original_lines[index + offset], block_lines[offset]) for offset in range(len(block_lines)))
    ]
    return sorted(positions, key=lambda index: (abs(index - preferred_start), index))


def _find_hunk_with_context_anchor(
    original_lines: list[str],
    hinted_start: int,
    cursor: int,
    hunk_lines: list[str],
    comparator: Callable[[str, str], bool],
) -> int | None:
    preferred_start = max(hinted_start, cursor)
    for source_offset, block_lines in _context_blocks(hunk_lines):
        for block_start in _block_positions(original_lines, block_lines, preferred_start + source_offset, comparator)[:10]:
            candidate = block_start - source_offset
            if candidate < cursor:
                continue
            if _matches_hunk_at(original_lines, candidate, hunk_lines, comparator):
                return candidate
    return None


def _search_indices(start: int, end: int, preferred: int) -> list[int]:
    return sorted(range(start, end + 1), key=lambda index: (abs(index - preferred), index))


def _find_hunk_start(original_lines: list[str], hinted_start: int, cursor: int, hunk_lines: list[str]) -> int | None:
    preferred_start = max(hinted_start, cursor)

    for comparator in (_line_equals, _normalized_line_equals):
        if _matches_hunk_at(original_lines, preferred_start, hunk_lines, comparator):
            return preferred_start

        window_start = max(cursor, preferred_start - 120)
        window_end = min(len(original_lines), preferred_start + 120)
        for index in _search_indices(window_start, window_end, preferred_start):
            if _matches_hunk_at(original_lines, index, hunk_lines, comparator):
                return index

        anchored = _find_hunk_with_context_anchor(original_lines, hinted_start, cursor, hunk_lines, comparator)
        if anchored is not None:
            return anchored

        for index in range(cursor, len(original_lines) + 1):
            if _matches_hunk_at(original_lines, index, hunk_lines, comparator):
                return index

    return None


@dataclass
class DiffValidation:
    applicable: bool
    reason: str
    hunk_issues: list[str] = field(default_factory=list)


def validate_diff(original_text: str, diff_text: str) -> DiffValidation:
    """
    Pre-validate a diff against the current file content without applying it.
    Returns a DiffValidation indicating whether all hunks can be located.
    Call this before apply_unified_diff to get actionable errors early.
    """
    try:
        hunks = _parse_unified_diff(diff_text)
    except ValueError as e:
        return DiffValidation(applicable=False, reason=f"Invalid diff format: {e}")

    original_lines = original_text.splitlines()
    cursor = 0
    hunk_issues: list[str] = []

    for i, hunk in enumerate(hunks):
        start_index = _find_hunk_start(original_lines, hunk.old_start, cursor, hunk.lines)
        if start_index is None:
            context_lines = [l[1:] for l in hunk.lines if l.startswith(" ")]
            preview = repr(context_lines[0]) if context_lines else "<no context lines in hunk>"
            hunk_issues.append(
                f"Hunk {i + 1} (near file line {hunk.old_start + 1}): context not found. "
                f"First context line expected: {preview}"
            )
            # Advance cursor by best guess so we keep checking remaining hunks
            consumed = sum(1 for l in hunk.lines if l[:1] in (" ", "-"))
            cursor = max(cursor, hunk.old_start + consumed)
        else:
            consumed = sum(1 for l in hunk.lines if l[:1] in (" ", "-"))
            cursor = start_index + consumed

    if hunk_issues:
        return DiffValidation(
            applicable=False,
            reason=f"{len(hunk_issues)} of {len(hunks)} hunk(s) could not be located in the file",
            hunk_issues=hunk_issues,
        )
    return DiffValidation(applicable=True, reason=f"all {len(hunks)} hunk(s) located")


def analyze_unified_diff(original_text: str, diff_text: str, path: str) -> DiffStats:
    hunks = _parse_unified_diff(diff_text)
    original_lines = original_text.splitlines()

    added_line_count = 0
    removed_line_count = 0
    context_line_count = 0
    largest_hunk_change = 0
    span_start: int | None = None
    span_end = 0

    for hunk in hunks:
        hunk_added = sum(1 for line in hunk.lines if line.startswith("+"))
        hunk_removed = sum(1 for line in hunk.lines if line.startswith("-"))
        hunk_context = sum(1 for line in hunk.lines if line.startswith(" "))
        added_line_count += hunk_added
        removed_line_count += hunk_removed
        context_line_count += hunk_context
        largest_hunk_change = max(largest_hunk_change, hunk_added + hunk_removed)

        current_span_start = hunk.old_start
        current_span_end = hunk.old_start + max(hunk.old_count, hunk.new_count, 1)
        span_start = current_span_start if span_start is None else min(span_start, current_span_start)
        span_end = max(span_end, current_span_end)

    file_line_count = len(original_lines)
    changed_line_count = added_line_count + removed_line_count
    span_line_count = max(span_end - (span_start or 0), 0)
    baseline = max(file_line_count, 1)

    return DiffStats(
        path=path,
        file_line_count=file_line_count,
        hunk_count=len(hunks),
        changed_line_count=changed_line_count,
        added_line_count=added_line_count,
        removed_line_count=removed_line_count,
        context_line_count=context_line_count,
        largest_hunk_change=largest_hunk_change,
        span_line_count=span_line_count,
        change_ratio=changed_line_count / baseline,
        span_ratio=span_line_count / baseline,
    )


def choose_patch_strategy(stats: DiffStats) -> tuple[str, str]:
    if stats.changed_line_count == 0:
        return "diff", "The diff does not change any lines."

    reasons: list[str] = []
    if stats.changed_line_count > 300:
        reasons.append(f"it changes {stats.changed_line_count} lines")
    if stats.change_ratio > 0.55 and stats.changed_line_count > 100:
        reasons.append(f"it touches {stats.change_ratio:.0%} of the file")
    if stats.hunk_count > 14:
        reasons.append(f"it spans {stats.hunk_count} hunks")
    if stats.hunk_count > 6 and stats.span_ratio > 0.7:
        reasons.append(f"the hunks are spread across {stats.span_ratio:.0%} of the file")
    if stats.largest_hunk_change > 150:
        reasons.append(f"one hunk alone changes {stats.largest_hunk_change} lines")

    if reasons:
        return "rewrite", "; ".join(reasons)

    return (
        "diff",
        f"targeted patch with {stats.changed_line_count} changed lines across {stats.hunk_count} hunks",
    )


def apply_unified_diff(original_text: str, diff_text: str, path: str) -> str:
    original_lines = original_text.splitlines()
    hunks = _parse_unified_diff(diff_text)

    result: list[str] = []
    cursor = 0

    for hunk_index, hunk in enumerate(hunks):
        start_index = _find_hunk_start(original_lines, hunk.old_start, cursor, hunk.lines)
        if start_index is None:
            context_lines = [l[1:] for l in hunk.lines if l.startswith(" ")][:3]
            context_preview = repr(context_lines[0]) if context_lines else "<no context lines>"
            raise ValueError(
                f"Hunk {hunk_index + 1} (near line {hunk.old_start + 1}) could not be located in the file. "
                f"The context lines do not match the current file content — the file may have changed. "
                f"Re-read the file and regenerate the diff. First context line expected: {context_preview}"
            )
        if start_index < cursor:
            raise ValueError(
                f"Hunk {hunk_index + 1} overlaps with a previous hunk (starts at line {start_index + 1}, "
                f"cursor is at line {cursor + 1}). Hunks must be in order."
            )
        result.extend(original_lines[cursor:start_index])
        source_index = start_index
        for line_index, hunk_line in enumerate(hunk.lines):
            marker = hunk_line[:1]
            body = hunk_line[1:]
            if marker == " ":
                if source_index >= len(original_lines):
                    raise ValueError(
                        f"Hunk {hunk_index + 1}, line {line_index + 1}: context line {body!r} "
                        f"expected at file line {source_index + 1} but file ended."
                    )
                if not _normalized_line_equals(original_lines[source_index], body):
                    raise ValueError(
                        f"Hunk {hunk_index + 1}, line {line_index + 1}: context mismatch at file line {source_index + 1}. "
                        f"Expected: {body!r}. Found: {original_lines[source_index]!r}. "
                        f"Re-read the file and regenerate the diff."
                    )
                result.append(original_lines[source_index])
                source_index += 1
            elif marker == "-":
                if source_index >= len(original_lines):
                    raise ValueError(
                        f"Hunk {hunk_index + 1}, line {line_index + 1}: tried to delete {body!r} "
                        f"at file line {source_index + 1} but file ended."
                    )
                if not _normalized_line_equals(original_lines[source_index], body):
                    raise ValueError(
                        f"Hunk {hunk_index + 1}, line {line_index + 1}: delete mismatch at file line {source_index + 1}. "
                        f"Expected to delete: {body!r}. Found: {original_lines[source_index]!r}. "
                        f"Re-read the file and regenerate the diff."
                    )
                source_index += 1
            elif marker == "+":
                result.append(body)
            else:
                raise ValueError(f"Unsupported diff line marker {marker!r} in hunk {hunk_index + 1}")
        cursor = source_index

    result.extend(original_lines[cursor:])
    updated_text = "\n".join(result)
    if original_text.endswith("\n") or diff_text.endswith("\n"):
        updated_text += "\n"

    if Path(path).suffix == ".json":
        try:
            json.loads(updated_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Diff produced invalid JSON: {e}") from e

    return updated_text
