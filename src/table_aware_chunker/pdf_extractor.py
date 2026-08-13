"""Extract text and tabular structure from uploaded PDF documents."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import statistics
from collections.abc import Callable, Sequence
from pathlib import Path

from .blocks import (
    SavedStructuredCorpus,
    clean_text,
    make_table_block,
    make_text_block,
    save_structured_corpus,
)


Progress = Callable[[str], None]
PDF_CORPUS_VERSION = "v9-pdf-text-paragraphs"

_GROUPED_NUMBER = re.compile(r"(?<!\w)(\d{1,3}(?:[\s\u00a0]\d{3})+)(?!\w)")

# Geometric ordering avoids the one-character fragments produced when
# ``use_text_flow=True`` encounters 90-degree table headings.  The rotated
# direction settings make bottom-to-top labels such as "Normál tengelytáv"
# available as complete words for header reconstruction.
_PDF_WORD_OPTIONS = {
    "use_text_flow": False,
    "line_dir_rotated": "ltr",
    "char_dir_rotated": "btt",
}


def parse_pdf_paths(value, max_files: int, max_file_bytes: int) -> list[Path]:
    """
    Validate uploaded PDF paths and configured size/count limits.

    Inputs:
        value: Gradio single path, path list, or empty value.
        max_files: Maximum number of PDFs in one corpus.
        max_file_bytes: Maximum permitted size of each PDF.

    Returns:
        Distinct resolved PDF paths in UI order.
    """
    raw_values = value if isinstance(value, (list, tuple)) else [value]
    paths: list[Path] = []
    seen: set[Path] = set()
    for raw in raw_values:
        if not raw:
            continue
        path = Path(str(raw)).resolve()
        if path in seen:
            continue
        if not path.is_file():
            raise ValueError(f"Uploaded PDF is unavailable: {path}")
        with path.open("rb") as stream:
            signature = stream.read(5)
        if path.suffix.casefold() != ".pdf" or signature != b"%PDF-":
            raise ValueError(f"Uploaded file is not a valid PDF: {path.name}")
        if path.stat().st_size > max_file_bytes:
            raise ValueError(
                f"{path.name} exceeds the {max_file_bytes:,}-byte per-file limit"
            )
        paths.append(path)
        seen.add(path)
    if not paths:
        raise ValueError("Upload at least one PDF document.")
    if len(paths) > max_files:
        raise ValueError(f"At most {max_files} PDF documents can be indexed at once.")
    return paths


def pdf_request_key(paths: Sequence[Path]) -> str:
    """
    Derive a stable identifier from ordered PDF bytes.

    Inputs:
        paths: Validated PDF paths.

    Returns:
        A deterministic hexadecimal identifier for the uploaded files.
    """
    digest = hashlib.sha256()
    digest.update(len(paths).to_bytes(4, "big"))
    for path in paths:
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()[:16]


def _pdf_cache_pointer(root: Path, request_key: str) -> Path:
    """
    Locate the persistent request-to-corpus pointer for one PDF selection.

    Inputs:
        root: Parent directory for saved PDF corpora.
        request_key: Content identifier returned by ``pdf_request_key``.

    Returns:
        JSON pointer path scoped to the current PDF extraction version.
    """
    pointer_key = hashlib.sha256(
        f"{PDF_CORPUS_VERSION}|{request_key}".encode("utf-8")
    ).hexdigest()[:16]
    return root / "_requests" / f"{pointer_key}.json"


def _load_cached_pdf_corpus(
    root: Path, request_key: str
) -> SavedStructuredCorpus | None:
    """
    Load an already materialized PDF corpus without parsing the PDF again.

    Inputs:
        root: Parent directory for saved PDF corpora.
        request_key: Byte-based identifier for the uploaded PDF selection.

    Returns:
        Saved corpus description, or ``None`` when the pointer is absent or
        incomplete.
    """
    pointer = _pdf_cache_pointer(root, request_key)
    if not pointer.exists():
        return None
    try:
        value = json.loads(pointer.read_text(encoding="utf-8"))
        corpus_id = str(value["corpus_id"])
        if not re.fullmatch(r"[0-9a-f]{16}", corpus_id):
            return None
        corpus_dir = root / corpus_id
        corpus_path = corpus_dir / "corpus.txt"
        blocks_path = corpus_dir / "blocks.json"
        sources_path = corpus_dir / "sources.json"
        if not all(path.is_file() for path in (corpus_path, blocks_path, sources_path)):
            return None
        return SavedStructuredCorpus(
            corpus_path=corpus_path,
            blocks_path=blocks_path,
            sources_path=sources_path,
            request_key=request_key,
            content_key=str(value["content_key"]),
            source_count=int(value["source_count"]),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _save_pdf_cache_pointer(root: Path, saved: SavedStructuredCorpus) -> None:
    """
    Persist the request-to-corpus pointer atomically.

    Inputs:
        root: Parent directory for saved PDF corpora.
        saved: Newly materialized corpus description.

    Returns:
        None.
    """
    pointer = _pdf_cache_pointer(root, saved.request_key)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "corpus_id": saved.corpus_path.parent.name,
        "content_key": saved.content_key,
        "source_count": saved.source_count,
    }
    temporary = pointer.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(pointer)


def _inside_any_table(word: dict, boxes: Sequence[tuple]) -> bool:
    """
    Test whether the center of one positioned PDF word is in a table.

    Inputs:
        word: ``pdfplumber`` word dictionary with bounding coordinates.
        boxes: Detected table bounding boxes as ``x0, top, x1, bottom``.

    Returns:
        ``True`` when the word center is inside at least one table box.
    """
    center_x = (float(word["x0"]) + float(word["x1"])) / 2
    center_y = (float(word["top"]) + float(word["bottom"])) / 2
    return any(
        x0 <= center_x <= x1 and top <= center_y <= bottom
        for x0, top, x1, bottom in boxes
    )


def _positioned_line_text(words: Sequence[dict]) -> str:
    """Join one visual line, repairing word fragments with no visible gap."""
    ordered = sorted(words, key=lambda item: float(item["x0"]))
    if not ordered:
        return ""
    text = str(ordered[0].get("text", ""))
    previous = ordered[0]
    for word in ordered[1:]:
        gap = float(word["x0"]) - float(previous["x1"])
        height = min(
            float(previous["bottom"]) - float(previous["top"]),
            float(word["bottom"]) - float(word["top"]),
        )
        separator = "" if gap <= max(0.5, height * 0.06) else " "
        text += separator + str(word.get("text", ""))
        previous = word
    return clean_text(text)


def _join_wrapped_pdf_lines(lines: Sequence[str]) -> str:
    """Join wrapped visual lines and remove hyphens used only at line ends."""
    joined = ""
    for line in (clean_text(value) for value in lines):
        if not line:
            continue
        if joined.endswith("-"):
            joined = joined[:-1].rstrip() + line
        else:
            joined = f"{joined} {line}".strip()
    return clean_text(joined)


def _group_pdf_lines(words: Sequence[dict], tolerance: float = 3.0) -> list[tuple[float, str]]:
    """
    Reconstruct approximate text lines from positioned PDF words.

    Inputs:
        words: ``pdfplumber`` word dictionaries outside detected tables.
        tolerance: Maximum vertical difference within one line.

    Returns:
        Pairs of top coordinate and left-to-right line text.
    """
    lines: list[tuple[float, list[dict]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if lines and abs(top - lines[-1][0]) <= tolerance:
            lines[-1][1].append(word)
        else:
            lines.append((top, [word]))
    return [
        (
            top,
            _positioned_line_text(line_words),
        )
        for top, line_words in lines
    ]


def _word_key(word: dict) -> tuple:
    """
    Return a stable key for one positioned word on a page.

    Inputs:
        word: ``pdfplumber`` word dictionary.

    Returns:
        Text and rounded coordinates suitable for set membership.
    """
    return (
        str(word.get("text", "")),
        round(float(word["x0"]), 3),
        round(float(word["top"]), 3),
        round(float(word["x1"]), 3),
        round(float(word["bottom"]), 3),
    )


def _word_center(word: dict) -> tuple[float, float]:
    """
    Calculate the center point of a positioned word.

    Inputs:
        word: ``pdfplumber`` word dictionary.

    Returns:
        Horizontal and vertical center coordinates.
    """
    return (
        (float(word["x0"]) + float(word["x1"])) / 2,
        (float(word["top"]) + float(word["bottom"])) / 2,
    )


def _unique_coordinates(values: Sequence[float], tolerance: float = 1.0) -> list[float]:
    """
    Merge nearly identical PDF drawing coordinates.

    Inputs:
        values: Raw horizontal or vertical coordinates.
        tolerance: Maximum distance treated as the same coordinate.

    Returns:
        Sorted representative coordinates.
    """
    result: list[float] = []
    for value in sorted(float(item) for item in values):
        if not result or abs(value - result[-1]) > tolerance:
            result.append(value)
        else:
            result[-1] = (result[-1] + value) / 2
    return result


def _infer_left_header_boundaries(
    words: Sequence[dict], row_band: tuple[float, float], table_left: float
) -> list[float]:
    """
    Infer separators between several unruled columns left of a detected grid.

    Some PDF tables draw horizontal rules but no vertical rules for their first
    columns. The detector can then start at a later ruled column. Distinct word
    groups in the first row, which is commonly the visible header row, provide
    centers from which the omitted separators can be estimated.

    Inputs:
        words: Complete positioned page words.
        row_band: Top and bottom of the detector's first physical row.
        table_left: Left edge of the detected partial grid.

    Returns:
        Estimated separator coordinates between left-side header groups.
    """
    top, bottom = row_band
    candidates = sorted(
        (
            word
            for word in words
            if word.get("upright", True)
            and top <= _word_center(word)[1] <= bottom
            and _word_center(word)[0] < table_left
        ),
        key=lambda word: float(word["x0"]),
    )
    if len(candidates) < 2:
        return []

    groups: list[list[dict]] = [[candidates[0]]]
    for word in candidates[1:]:
        previous_right = max(float(item["x1"]) for item in groups[-1])
        if float(word["x0"]) - previous_right > 18.0:
            groups.append([word])
        else:
            groups[-1].append(word)

    boundaries: list[float] = []
    for left_group, right_group in zip(groups, groups[1:]):
        left_center = max(_word_center(word)[0] for word in left_group)
        right_center = min(_word_center(word)[0] for word in right_group)
        boundaries.append((left_center + right_center) / 2)
    return boundaries


def _words_in_box(words: Sequence[dict], box: tuple[float, float, float, float]) -> list[dict]:
    """
    Select words whose centers lie in a rectangular PDF region.

    Inputs:
        words: Positioned page words.
        box: ``x0, top, x1, bottom`` rectangle.

    Returns:
        Words inside the rectangle.
    """
    x0, top, x1, bottom = box
    return [
        word
        for word in words
        if x0 <= _word_center(word)[0] <= x1
        and top <= _word_center(word)[1] <= bottom
    ]


def _positioned_words_text(words: Sequence[dict]) -> str:
    """
    Join upright or rotated words in their human reading order.

    Inputs:
        words: Positioned words belonging to one cell or header.

    Returns:
        Clean, space-separated text.
    """
    if not words:
        return ""
    upright = [word for word in words if word.get("upright", True)]
    rotated = [word for word in words if not word.get("upright", True)]
    parts: list[str] = []
    if upright:
        parts.extend(text for _, text in _group_pdf_lines(upright) if text)
    if rotated:
        # Words in a bottom-to-top label are read from the greatest ``top``
        # coordinate upward. Distinct vertical labels are ordered left-to-right.
        columns: list[tuple[float, list[dict]]] = []
        for word in sorted(rotated, key=lambda item: float(item["x0"])):
            x0 = float(word["x0"])
            if columns and abs(x0 - columns[-1][0]) <= 3.0:
                columns[-1][1].append(word)
            else:
                columns.append((x0, [word]))
        for _, column_words in columns:
            parts.append(
                " ".join(
                    str(word.get("text", ""))
                    for word in sorted(
                        column_words, key=lambda item: float(item["top"]), reverse=True
                    )
                )
            )
    text = clean_text(" ".join(parts))
    return _GROUPED_NUMBER.sub(
        lambda match: re.sub(r"[\s\u00a0]", "", match.group(1)), text
    )


def _positioned_line_groups(
    words: Sequence[dict], tolerance: float = 3.0
) -> list[list[dict]]:
    """
    Group upright words into visual lines while preserving coordinates.

    Inputs:
        words: Positioned words from one table cell.
        tolerance: Maximum ``top`` difference within one visual line.

    Returns:
        Top-to-bottom word groups, each ordered left-to-right.
    """
    lines: list[tuple[float, list[dict]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if lines and abs(top - lines[-1][0]) <= tolerance:
            lines[-1][1].append(word)
        else:
            lines.append((top, [word]))
    return [
        sorted(line_words, key=lambda item: float(item["x0"]))
        for _, line_words in lines
    ]


def _line_column_gutter(line: Sequence[dict]) -> tuple[float, float] | None:
    """Return the strongest whitespace gutter separating one line's columns."""
    ordered = sorted(line, key=lambda item: float(item["x0"]))
    if len(ordered) < 2:
        return None
    gaps = [
        (
            float(right["x0"]) - float(left["x1"]),
            float(left["x1"]),
            float(right["x0"]),
        )
        for left, right in zip(ordered, ordered[1:])
    ]
    positive_gaps = [gap for gap, _, _ in gaps if gap > 0]
    if not positive_gaps:
        return None
    word_heights = [
        float(word["bottom"]) - float(word["top"])
        for word in ordered
    ]
    minimum = max(
        6.0,
        statistics.median(word_heights) * 0.6,
        statistics.median(positive_gaps) * 2.5,
    )
    gap, left, right = max(gaps, key=lambda item: item[0])
    return (left, right) if gap >= minimum else None


def _group_pdf_text_lines(
    words: Sequence[dict], tolerance: float = 3.0
) -> list[tuple[float, str]]:
    """
    Reconstruct page text while reading recurring columns independently.

    Two or more consecutive visual lines whose strongest whitespace gaps
    overlap are treated as a column region. The left column is emitted from
    top to bottom before the right column. Full-width text before and after the
    region retains its ordinary geometric order.
    """
    lines = _positioned_line_groups(words, tolerance)
    result: list[dict] = []

    def append_full_width_line(line: Sequence[dict]) -> None:
        """Append or merge one ordinary wrapped line into a paragraph."""
        top = min(float(word["top"]) for word in line)
        bottom = max(float(word["bottom"]) for word in line)
        left = min(float(word["x0"]) for word in line)
        height = bottom - top
        text = _positioned_words_text(line)
        if result and result[-1]["kind"] == "full":
            previous = result[-1]
            vertical_gap = top - float(previous["bottom"])
            aligned = abs(left - float(previous["left"])) <= max(
                12.0, height * 1.5
            )
            if vertical_gap <= max(4.0, height * 0.3) and aligned:
                previous["text"] = _join_wrapped_pdf_lines(
                    [str(previous["text"]), text]
                )
                previous["bottom"] = bottom
                return
        result.append(
            {
                "kind": "full",
                "top": top,
                "bottom": bottom,
                "left": left,
                "text": text,
            }
        )

    index = 0
    while index < len(lines):
        gutter = _line_column_gutter(lines[index])
        if gutter is None:
            append_full_width_line(lines[index])
            index += 1
            continue

        gutter_left, gutter_right = gutter
        end = index + 1
        previous_bottom = max(float(word["bottom"]) for word in lines[index])
        while end < len(lines):
            next_gutter = _line_column_gutter(lines[end])
            next_top = min(float(word["top"]) for word in lines[end])
            typical_height = statistics.median(
                float(word["bottom"]) - float(word["top"])
                for word in lines[end]
            )
            if (
                next_gutter is None
                or next_top - previous_bottom > max(8.0, typical_height * 1.5)
            ):
                break
            overlap_left = max(gutter_left, next_gutter[0])
            overlap_right = min(gutter_right, next_gutter[1])
            if overlap_right - overlap_left < 4.0:
                break
            gutter_left, gutter_right = overlap_left, overlap_right
            previous_bottom = max(float(word["bottom"]) for word in lines[end])
            end += 1

        if end - index < 2:
            append_full_width_line(lines[index])
            index += 1
            continue

        divider = (gutter_left + gutter_right) / 2
        first_top = min(float(word["top"]) for word in lines[index])
        for side_number, side in enumerate(("left", "right")):
            selected_lines: list[str] = []
            for line in lines[index:end]:
                selected = [
                    word
                    for word in line
                    if (_word_center(word)[0] < divider) == (side == "left")
                ]
                if selected:
                    selected_lines.append(_positioned_words_text(selected))
            if selected_lines:
                result.append(
                    {
                        "kind": "column",
                        "top": first_top + side_number * 0.001,
                        "bottom": previous_bottom,
                        "left": min(
                            float(word["x0"])
                            for line in lines[index:end]
                            for word in line
                            if (_word_center(word)[0] < divider)
                            == (side == "left")
                        ),
                        "text": _join_wrapped_pdf_lines(selected_lines),
                    }
                )
        index = end
    return [
        (float(item["top"]), str(item["text"]))
        for item in result
        if item["text"]
    ]


def _horizontal_word_groups(
    words: Sequence[dict], minimum_gap: float = 18.0
) -> list[list[dict]]:
    """Split one visual line at the large gaps separating repeated cards."""
    ordered = sorted(words, key=lambda item: float(item["x0"]))
    if not ordered:
        return []
    groups: list[list[dict]] = [[ordered[0]]]
    for word in ordered[1:]:
        previous_right = max(float(item["x1"]) for item in groups[-1])
        if float(word["x0"]) - previous_right > minimum_gap:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def _reconstruct_label_value_grid(
    table,
    words: Sequence[dict],
    section_rows: Sequence[tuple[float, str]],
    column_count: int,
) -> tuple[list[list[str]], set[tuple]] | None:
    """
    Convert a repeated visual card grid into independent label/value rows.

    Some brochures draw several cards across the page, with a descriptive line
    and a numeric value under each card. Decorative image and underline edges
    can make a PDF table detector report many narrow, empty columns. This
    layout-only rule pairs adjacent label and value lines without interpreting
    what their text means.
    """
    table_left, table_top, table_right, table_bottom = map(float, table.bbox)
    has_leading_section = any(
        abs(float(section_top) - table_top) <= 2.0
        for section_top, _ in section_rows
    )
    if column_count < 4 or not has_leading_section:
        return None

    positioned = [
        word
        for word in words
        if word.get("upright", True)
        and table_left <= _word_center(word)[0] <= table_right
        and table_top <= _word_center(word)[1] <= table_bottom
    ]
    lines = _positioned_line_groups(positioned)
    content_lines: list[list[dict]] = []
    for line in lines:
        line_text = _positioned_words_text(line)
        is_section = any(
            line_text == section_text
            for _, section_text in section_rows
        )
        if not is_section:
            content_lines.append(line)

    reconstructed: list[list[str]] = []
    consumed: set[tuple] = set()
    index = 0
    while index + 1 < len(content_lines):
        label_line = content_lines[index]
        value_line = content_lines[index + 1]
        vertical_gap = min(float(word["top"]) for word in value_line) - max(
            float(word["bottom"]) for word in label_line
        )
        label_groups = _horizontal_word_groups(label_line)
        value_groups = _horizontal_word_groups(value_line)
        value_texts = [_positioned_words_text(group) for group in value_groups]
        if (
            -1.0 <= vertical_gap <= 18.0
            and len(label_groups) == len(value_groups)
            and value_texts
            and all(
                any(character.isdigit() for character in value)
                for value in value_texts
            )
        ):
            reconstructed.extend(
                [
                    _positioned_words_text(label_group),
                    value_text,
                ]
                for label_group, value_text in zip(label_groups, value_texts)
            )
            consumed.update(_word_key(word) for word in label_line)
            consumed.update(_word_key(word) for word in value_line)
            index += 2
            continue
        return None

    if index != len(content_lines) or len(reconstructed) < 3:
        return None
    return reconstructed, consumed


def _split_physical_table_row(cell_words: Sequence[Sequence[dict]]) -> list[list[str]]:
    """
    Split one ruled row when its price column contains several visual variants.

    Some specification PDFs omit horizontal separators between variants of
    one product. The product name and code span the complete ruled row, while
    availability markers and prices appear on distinct baselines. Each price
    baseline becomes a logical row; the shared name and code are repeated.

    Inputs:
        cell_words: Positioned words grouped by table column.

    Returns:
        One or more logical value rows.
    """
    values = [_positioned_words_text(words) for words in cell_words]
    if len(cell_words) < 2:
        return [values]

    price_lines = _positioned_line_groups(
        [word for word in cell_words[-1] if word.get("upright", True)]
    )
    price_values = [_positioned_words_text(line) for line in price_lines]
    if len(price_lines) < 2 or not all(
        re.fullmatch(r"\d+(?:[.,]\d+)?", value) for value in price_values
    ):
        return [values]

    anchors = [
        sum(_word_center(word)[1] for word in line) / len(line)
        for line in price_lines
    ]
    identity_columns = min(2, len(cell_words) - 1)
    split_rows = [[""] * len(cell_words) for _ in anchors]
    for column in range(identity_columns):
        for row in split_rows:
            row[column] = values[column]

    for column in range(identity_columns, len(cell_words)):
        line_groups = _positioned_line_groups(
            [word for word in cell_words[column] if word.get("upright", True)]
        )
        assigned: list[list[dict]] = [[] for _ in anchors]
        for line in line_groups:
            line_center = sum(_word_center(word)[1] for word in line) / len(line)
            nearest = min(
                range(len(anchors)), key=lambda index: abs(anchors[index] - line_center)
            )
            assigned[nearest].extend(line)
        for row_index, selected_words in enumerate(assigned):
            split_rows[row_index][column] = _positioned_words_text(selected_words)
    return split_rows


def _nearest_upright_header_words(words: Sequence[dict]) -> list[dict]:
    """
    Keep the closest contiguous upright text block above a table.

    Inputs:
        words: Candidate upright words from one table column.

    Returns:
        Words belonging to the nearest header block, excluding an earlier
        section title separated by a larger vertical gap.
    """
    if not words:
        return []
    lines: list[tuple[float, float, list[dict]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        bottom = float(word["bottom"])
        if lines and abs(top - lines[-1][0]) <= 3.0:
            line_top, line_bottom, line_words = lines[-1]
            line_words.append(word)
            lines[-1] = (line_top, max(line_bottom, bottom), line_words)
        else:
            lines.append((top, bottom, [word]))

    selected = [lines[-1]]
    for line in reversed(lines[:-1]):
        nearest_top = selected[-1][0]
        vertical_gap = nearest_top - line[1]
        if vertical_gap > 8.0:
            break
        selected.append(line)
    selected.reverse()
    return [word for _, _, line_words in selected for word in line_words]


def _reconstruct_pdf_table(
    table,
    words: Sequence[dict],
    table_boxes: Sequence[tuple] = (),
    reconstruction_info: dict[str, bool] | None = None,
) -> tuple[list[str], list[list[str]], list[tuple[float, str]], set[tuple]]:
    """
    Reconstruct a detected table from page word geometry.

    ``pdfplumber`` can detect a table's ruled numeric columns while returning
    ``None`` for an unruled first column. It can also begin the detected box
    below the visible header. This helper derives the complete column grid,
    fills every cell from word centers, and reads the nearest preceding header
    block for each column.

    Inputs:
        table: ``pdfplumber`` detected table with bounding boxes and rows.
        words: Positioned words from the complete page.
        table_boxes: Other detected table boxes used to prevent expansion into
            a side-by-side table.
        reconstruction_info: Optional output dictionary describing layout
            decisions needed by document-level caption handling.

    Returns:
        Headers, data rows, merged section rows as ``top, text`` pairs, and
        keys of page words consumed by the reconstructed table.
    """
    coordinates = [float(table.bbox[0]), float(table.bbox[2])]
    row_bands: list[tuple[float, float]] = []
    for table_row in table.rows:
        present_cells = [cell for cell in table_row.cells if cell is not None]
        if present_cells:
            row_bands.append(
                (
                    min(float(cell[1]) for cell in present_cells),
                    max(float(cell[3]) for cell in present_cells),
                )
            )
        for cell in present_cells:
            coordinates.extend((float(cell[0]), float(cell[2])))

    # A table detector may omit an unruled edge column entirely. Words aligned
    # with detected rows reveal and restore such a column, as happens with the
    # vehicle-name column in the supplied price-list PDF.
    aligned_words = [
        word
        for word in words
        if any(top <= _word_center(word)[1] <= bottom for top, bottom in row_bands)
    ]
    table_left = float(table.bbox[0])
    table_top = float(table.bbox[1])
    table_bottom = float(table.bbox[3])
    left_peer_edges = [
        float(box[2])
        for box in table_boxes
        if float(box[2]) <= table_left
        and min(float(box[3]), table_bottom) > max(float(box[1]), table_top)
        if tuple(box) != tuple(table.bbox)
    ]
    left_limit = max(left_peer_edges, default=float("-inf"))
    left_words = [
        word
        for word in aligned_words
        if left_limit < _word_center(word)[0] < table_left
    ]
    if left_words:
        coordinates.append(min(float(word["x0"]) for word in left_words))
        if row_bands:
            safe_left_words = [
                word for word in words if _word_center(word)[0] > left_limit
            ]
            coordinates.extend(
                _infer_left_header_boundaries(
                    safe_left_words,
                    row_bands[0],
                    table_left,
                )
            )
    columns = _unique_coordinates(coordinates)
    if len(columns) < 2:
        return [], [], [], set()

    rows: list[list[str]] = []
    section_rows: list[tuple[float, str]] = []
    consumed_words: set[tuple] = set()
    for table_row in table.rows:
        cells = [cell for cell in table_row.cells if cell is not None]
        if not cells:
            continue
        row_top = min(float(cell[1]) for cell in cells)
        row_bottom = max(float(cell[3]) for cell in cells)
        cell_words = [
            _words_in_box(
                words,
                (columns[index], row_top, columns[index + 1], row_bottom),
            )
            for index in range(len(columns) - 1)
        ]
        consumed_words.update(
            _word_key(word) for selected_words in cell_words for word in selected_words
        )
        merged_across_table = (
            len(cells) == 1
            and float(cells[0][2]) - float(cells[0][0])
            >= float(table.bbox[2]) - float(table.bbox[0]) - 1.0
        )
        complete_row_text = _positioned_words_text(
            _words_in_box(words, (columns[0], row_top, columns[-1], row_bottom))
        )
        if merged_across_table and complete_row_text:
            section_rows.append(
                (
                    row_top,
                    complete_row_text,
                )
            )
            continue

        for values in _split_physical_table_row(cell_words):
            populated = [value for value in values if value]
            if len(columns) == 2 or len(populated) >= 2:
                rows.append(values)
            elif populated:
                section_rows.append((row_top, populated[0]))

    label_value_grid = _reconstruct_label_value_grid(
        table,
        words,
        section_rows,
        len(columns) - 1,
    )
    if label_value_grid is not None:
        reconstructed_rows, reconstructed_words = label_value_grid
        consumed_words.update(reconstructed_words)
        if reconstruction_info is not None:
            reconstruction_info["leading_section_caption"] = True
        return ["Label", "Value"], reconstructed_rows, section_rows, consumed_words

    headers: list[str] = []
    selected_header_words: list[list[dict]] = []
    for index in range(len(columns) - 1):
        x0, x1 = columns[index], columns[index + 1]
        candidates = [
            word
            for word in words
            if table_top - 180.0 <= float(word["top"])
            and float(word["bottom"]) <= table_top + 1.0
            and x0 <= _word_center(word)[0] <= x1
        ]
        upright = _nearest_upright_header_words(
            [word for word in candidates if word.get("upright", True)]
        )
        rotated = [word for word in candidates if not word.get("upright", True)]
        selected = [*upright, *rotated]
        header = _positioned_words_text(selected)
        headers.append(header or f"Column {index + 1}")
        selected_header_words.append(selected)

    # A detector can begin at the top of the visible header row rather than
    # above it. Promote a dense first row when external header recovery is
    # mostly generic. Also prefer a compact first row over long, multi-line
    # prose that happens to sit directly above the table.
    promoted_header = False
    generic_headers = sum(header.startswith("Column ") for header in headers)
    prose_header_columns = sum(
        len([word for word in selected if word.get("upright", True)]) >= 6
        and len(
            _positioned_line_groups(
                [word for word in selected if word.get("upright", True)]
            )
        )
        >= 2
        for selected in selected_header_words
    )
    prose_external_headers = prose_header_columns >= max(
        2, (3 * len(headers) + 3) // 4
    )
    if rows:
        candidate = rows[0]
        populated = sum(bool(value) for value in candidate)
        required = max(2, (3 * len(headers) + 3) // 4)
        compact_header = (
            populated >= required
            and sum(
                bool(value)
                and not any(character.isdigit() for character in value)
                and len(value.split()) <= 6
                for value in candidate
            )
            >= required
        )
        should_promote = (
            populated >= required and generic_headers * 2 >= len(headers)
        ) or (prose_external_headers and compact_header)
        if should_promote:
            headers = [
                value or f"Column {index + 1}"
                for index, value in enumerate(candidate)
            ]
            rows = rows[1:]
            promoted_header = True
            if reconstruction_info is not None:
                reconstruction_info["promoted_internal_header"] = True
                if prose_external_headers:
                    reconstruction_info["rejected_prose_headers"] = True

    if not promoted_header or not prose_external_headers:
        consumed_words.update(
            _word_key(word)
            for selected in selected_header_words
            for word in selected
        )

    # Remove detector-only trailing regions that contain no header or row
    # content. Horizontal rules in the Opel fixtures extend beyond the final
    # semantic column and otherwise create an empty ``Column N``.
    while (
        headers
        and headers[-1].startswith("Column ")
        and all(not row[-1] for row in rows)
    ):
        headers.pop()
        for row in rows:
            row.pop()

    # A footer directly below a ruled table can be included as the detector's
    # final row. Promoted-header tables have dense data rows, so discard only
    # trailing rows that populate fewer than three quarters of their columns.
    if promoted_header and headers:
        required = max(2, (3 * len(headers) + 3) // 4)
        while rows and sum(bool(value) for value in rows[-1]) < required:
            rows.pop()
    return headers, rows, section_rows, consumed_words


def _clean_pdf_table(raw_rows: Sequence[Sequence[object]]) -> tuple[list[str], list[list[str]]]:
    """
    Normalize a PDF table using its first non-empty row as the header.

    Inputs:
        raw_rows: Cell matrix returned by ``pdfplumber``.

    Returns:
        A header list and remaining non-empty data rows.
    """
    rows = [
        [clean_text(cell) for cell in row]
        for row in raw_rows
        if row and any(clean_text(cell) for cell in row)
    ]
    if not rows:
        return [], []
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    if len(padded) == 1:
        return [f"Column {column + 1}" for column in range(width)], padded
    headers = padded[0]
    return headers, padded[1:]


def extract_pdf_blocks(path: Path, progress: Progress | None = None) -> tuple[list[dict], dict]:
    """
    Extract positioned text and detected tables from one text-based PDF.

    Inputs:
        path: Validated PDF document path.
        progress: Optional callback accepting status messages.

    Returns:
        Ordered common-representation blocks and source metadata.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "PDF support requires pdfplumber; run pip install -r requirements.txt"
        ) from exc

    blocks: list[dict] = []
    with pdfplumber.open(path) as document:
        page_count = len(document.pages)
        for page_number, page in enumerate(document.pages, start=1):
            if progress:
                progress(
                    f"Parsing {path.name} PDF pages: "
                    f"{page_number - 1}/{page_count} completed"
                )
            # Some generated PDFs paint identical characters twice. Removing
            # coincident glyphs prevents text such as ``LLiissttaaáárraakk``
            # while leaving genuinely repeated letters intact.
            try:
                content_page = page.dedupe_chars()
            except Exception:
                content_page = page
            try:
                found_tables = content_page.find_tables()
            except Exception:
                # Some unusual PDF drawing instructions defeat table detection;
                # retain the page text instead of failing the complete corpus.
                found_tables = []
            boxes = [tuple(table.bbox) for table in found_tables]
            words = content_page.extract_words(**_PDF_WORD_OPTIONS) or []
            table_details: list[
                tuple[
                    int,
                    object,
                    list[str],
                    list[list[str]],
                    list[tuple[float, str]],
                    dict[str, bool],
                ]
            ] = []
            consumed_table_words: set[tuple] = set()
            for table_number, table in enumerate(found_tables, start=1):
                reconstruction_info: dict[str, bool] = {}
                try:
                    headers, rows, section_rows, used_header_words = (
                        _reconstruct_pdf_table(
                            table,
                            words,
                            boxes,
                            reconstruction_info,
                        )
                    )
                except Exception:
                    # Retain the former extraction path as a conservative
                    # fallback for unusual table objects.
                    raw_rows = table.extract(**_PDF_WORD_OPTIONS) or []
                    headers, rows = _clean_pdf_table(raw_rows)
                    section_rows = []
                    used_header_words = set()
                    reconstruction_info = {}
                consumed_table_words.update(used_header_words)
                table_details.append(
                    (
                        table_number,
                        table,
                        headers,
                        rows,
                        section_rows,
                        reconstruction_info,
                    )
                )

            # Rotated words are used above to reconstruct table headers. They
            # are omitted from paragraph lines so vertical headings do not
            # become streams of isolated or reversed character fragments.
            outside_words = [
                word
                for word in words
                if word.get("upright", True)
                and not _inside_any_table(word, boxes)
                and _word_key(word) not in consumed_table_words
            ]
            positioned: list[tuple[float, dict]] = []
            lines = _group_pdf_text_lines(outside_words)
            for top, text in lines:
                if text:
                    positioned.append(
                        (
                            top,
                            make_text_block(
                                text,
                                path.name,
                                page=page_number,
                                block_type="paragraph",
                            ),
                        )
                    )

            section_labels = [
                section_row
                for _, _, _, _, table_sections, _ in table_details
                for section_row in table_sections
            ]
            caption_sources = [*lines, *section_labels]
            for (
                table_number,
                table,
                headers,
                rows,
                section_rows,
                reconstruction_info,
            ) in table_details:
                leading_sections = (
                    [
                        (top, text)
                        for top, text in section_rows
                        if abs(float(top) - float(table.bbox[1])) <= 2.0
                    ]
                    if reconstruction_info.get("leading_section_caption")
                    else []
                )
                eligible_caption_sources = (
                    section_labels
                    if reconstruction_info.get("rejected_prose_headers")
                    else caption_sources
                )
                preceding = [
                    (top, text)
                    for top, text in eligible_caption_sources
                    if top < float(table.bbox[1])
                ]
                caption_source = max(
                    [*preceding, *leading_sections],
                    default=(0.0, ""),
                    key=lambda item: item[0],
                )
                caption = caption_source[1]
                if rows:
                    block = make_table_block(
                        headers,
                        rows,
                        path.name,
                        table_id=f"{path.name}-page-{page_number}-table-{table_number}",
                        caption=caption,
                        page=page_number,
                    )
                    block["bbox"] = [round(float(value), 2) for value in table.bbox]
                    positioned.append((float(table.bbox[1]), block))
                for section_top, section_text in section_rows:
                    if (section_top, section_text) == caption_source:
                        continue
                    positioned.append(
                        (
                            section_top,
                            make_text_block(
                                section_text,
                                path.name,
                                page=page_number,
                                block_type="heading",
                            ),
                        )
                    )
            blocks.extend(block for _, block in sorted(positioned, key=lambda item: item[0]))
            if progress:
                progress(
                    f"Parsed {path.name} PDF pages: "
                    f"{page_number}/{page_count} completed"
                )

    if not blocks:
        raise ValueError(
            f"No readable text was extracted from {path.name}. "
            "The PDF may be scanned and require OCR."
        )
    source = {
        "kind": "pdf",
        "filename": path.name,
        "bytes": path.stat().st_size,
        "pages": page_count,
    }
    return blocks, source


def prepare_pdf_corpus(
    value,
    root: Path,
    max_files: int,
    max_file_bytes: int,
    progress: Progress | None = None,
) -> SavedStructuredCorpus:
    """
    Validate, extract, persist, and copy an uploaded PDF corpus.

    Inputs:
        value: Gradio PDF upload value.
        root: Parent directory for saved PDF corpora.
        max_files: Maximum number of uploaded documents.
        max_file_bytes: Maximum byte size of each document.
        progress: Optional callback accepting status messages.

    Returns:
        Description of the saved content-addressed structured corpus.
    """
    paths = parse_pdf_paths(value, max_files, max_file_bytes)
    request_key = pdf_request_key(paths)
    cached = _load_cached_pdf_corpus(root, request_key)
    if cached is not None:
        if progress:
            progress(
                f"Loaded cached PDF corpus from {cached.corpus_path.parent}"
            )
        return cached

    blocks: list[dict] = []
    sources: list[dict] = []
    for source_number, path in enumerate(paths, start=1):
        extracted, source = extract_pdf_blocks(path, progress)
        source["source"] = source_number
        source["stored_file"] = f"sources/{source_number:02d}_{path.name}"
        blocks.extend(extracted)
        sources.append(source)

    saved = save_structured_corpus(
        blocks,
        sources,
        root,
        request_key,
        format_version=PDF_CORPUS_VERSION,
    )
    source_dir = saved.corpus_path.parent / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    for source_number, path in enumerate(paths, start=1):
        shutil.copy2(path, source_dir / f"{source_number:02d}_{path.name}")
    _save_pdf_cache_pointer(root, saved)
    return saved
