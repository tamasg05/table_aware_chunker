"""Create word-based, structure-aware chunks from extracted data blocks."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from .blocks import (
    associate_table_headings,
    clean_text,
    load_structured_blocks,
    render_block,
    render_table_row,
    validate_blocks,
)


CHUNKS_SCHEMA_VERSION = "1.1"

# Keep letters, numbers, and internal apostrophes, but discard punctuation
# marks around words. Case is preserved so names remain readable.
_WORD_PATTERN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", flags=re.UNICODE)


def tokenize_words(text: str) -> list[str]:
    """
    Return word tokens without surrounding punctuation.

    Inputs:
        text: Text to tokenize.

    Returns:
        Word strings in source order, with case and internal apostrophes kept.
    """
    return _WORD_PATTERN.findall(text)


def _text_span(text: str, matches: list[re.Match], start: int, end: int) -> str:
    """Return the original text covered by a half-open word-token range."""
    if start >= end or start >= len(matches):
        return ""
    character_start = matches[start].start()
    character_end = matches[end].start() if end < len(matches) else len(text)
    return text[character_start:character_end].strip()


def chunk_words(text: str, size: int, overlap: int) -> list[dict]:
    """
    Divide text into overlapping word-based chunks.

    Inputs:
        text: Complete source text.
        size: Maximum number of word tokens in each chunk.
        overlap: Number of tokens repeated between consecutive chunks.

    Returns:
        Chunk dictionaries containing normalized ``text`` and the
        punctuation-preserving ``source_text``.
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("Require size > 0 and 0 <= overlap < size")
    matches = list(_WORD_PATTERN.finditer(text))
    words = [match.group(0) for match in matches]
    chunks: list[dict] = []
    step = size - overlap
    for chunk_id, start in enumerate(range(0, len(words), step)):
        end = min(start + size, len(words))
        part = words[start:end]
        if not part:
            break
        chunks.append(
            {
                "id": chunk_id,
                "text": " ".join(part),
                "source_text": _text_span(text, matches, start, end),
            }
        )
        if start + size >= len(words):
            break
    return chunks


def _copy_metadata(block: dict) -> dict:
    """
    Copy source metadata shared by chunks derived from one block.

    Inputs:
        block: Common-representation source block.

    Returns:
        Present, non-empty source and section metadata fields.
    """
    return {
        key: block[key]
        for key in ("source_name", "source_url", "page", "heading_path")
        if block.get(key) not in (None, "", [])
    }


def _same_source_location(first: dict, second: dict) -> bool:
    """Return whether two blocks belong to the same source and page."""
    return (
        first.get("source_name", "") == second.get("source_name", "")
        and first.get("source_url", "") == second.get("source_url", "")
        and first.get("page") == second.get("page")
    )


def _table_retains_introductory_block(block: dict, table: dict) -> bool:
    """Return whether a table already contains one preceding block's text."""
    if not _same_source_location(block, table):
        return False
    text = clean_text(block.get("text", ""))
    if not text:
        return False
    if text == clean_text(table.get("caption", "")):
        return True
    if block.get("type") != "heading":
        return False
    full_heading_path = [
        clean_text(item)
        for item in block.get("heading_path", [])
        if clean_text(item)
    ]
    full_heading_path.append(text)
    table_heading_path = [
        clean_text(item)
        for item in table.get("heading_path", [])
        if clean_text(item)
    ]
    return table_heading_path[: len(full_heading_path)] == full_heading_path


def _table_word_count(block: dict, rows: Sequence[Sequence[str]]) -> int:
    """
    Count serialized word tokens for a table-row selection.

    Inputs:
        block: Table block or column-group variant.
        rows: Rows to include in the estimate.

    Returns:
        Number of prototype word tokens in the serialized table text.
    """
    return len(tokenize_words(render_block(block, rows)))


def _table_column_groups(block: dict, size: int) -> list[dict]:
    """
    Split an exceptionally wide table into key-preserving column groups.

    The first column is treated as a row identifier and repeated in every
    group. Ordinary tables remain unchanged.

    Inputs:
        block: Normalized table block.
        size: Target maximum word-token count for one row plus its context.

    Returns:
        One or more table blocks containing subsets of the original columns.
    """
    headers = block.get("headers", [])
    rows = block.get("rows", [])
    width = len(headers)
    if width <= 1 or max((_table_word_count(block, [row]) for row in rows), default=0) <= size:
        return [block]

    groups: list[list[int]] = []
    current: list[int] = []
    for column in range(1, width):
        candidate = [0, *current, column]
        candidate_block = {
            **block,
            "headers": [headers[index] for index in candidate],
            "rows": [[row[index] for index in candidate] for row in rows],
        }
        largest_row = max(
            (_table_word_count(candidate_block, [row]) for row in candidate_block["rows"]),
            default=0,
        )
        if current and largest_row > size:
            groups.append([0, *current])
            current = [column]
        else:
            current.append(column)
    if current:
        groups.append([0, *current])

    variants: list[dict] = []
    for group_number, columns in enumerate(groups, start=1):
        variants.append(
            {
                **block,
                "table_id": f"{block.get('table_id', 'table')}-columns-{group_number}",
                "headers": [headers[index] for index in columns],
                "rows": [[row[index] for index in columns] for row in rows],
                "column_group": [headers[index] for index in columns],
            }
        )
    return variants


def _source_identity(block: dict) -> tuple[str, str]:
    """Return the fields identifying one source document."""
    return block.get("source_name", ""), block.get("source_url", "")


def _heading_path(block: dict) -> tuple[str, ...]:
    """Return one normalized heading path for compatibility comparisons."""
    return tuple(
        clean_text(item)
        for item in block.get("heading_path", [])
        if clean_text(item)
    )


def _heading_introduces_block(heading: dict, following: dict) -> bool:
    """Return whether a heading can be attached to the following text block."""
    if heading.get("type") != "heading":
        return False
    text = clean_text(heading.get("text", ""))
    if not text:
        return False
    parent = _heading_path(heading)
    full_path = (*parent, text)
    following_path = _heading_path(following)
    if following.get("type") == "heading":
        return following_path[: len(full_path)] == full_path
    return following_path == parent or following_path[: len(full_path)] == full_path


def _render_text_parts(blocks: Sequence[dict]) -> list[str]:
    """Render adjacent text blocks without repeating their shared heading path."""
    return [
        render_block(block) if index == 0 else clean_text(block.get("text", ""))
        for index, block in enumerate(blocks)
    ]


def _text_group_word_count(blocks: Sequence[dict]) -> int:
    """Count the rendered words in a pending text-block group."""
    return sum(len(tokenize_words(part)) for part in _render_text_parts(blocks))


def _compatible_text_block(
    pending: Sequence[dict],
    candidate: dict,
    max_page_span: int,
) -> bool:
    """Return whether a text block can safely join the pending text group."""
    if not pending or _source_identity(pending[-1]) != _source_identity(candidate):
        return False
    previous = pending[-1]
    if candidate.get("type") == "heading":
        if not _heading_introduces_block(previous, candidate):
            return False
    elif _heading_path(previous) != _heading_path(candidate):
        if not _heading_introduces_block(previous, candidate):
            return False

    previous_page = previous.get("page")
    candidate_page = candidate.get("page")
    if previous_page is None or candidate_page is None:
        return previous_page is candidate_page
    if candidate_page < previous_page or candidate_page > previous_page + 1:
        return False
    pages = [
        block.get("page")
        for block in [*pending, candidate]
        if isinstance(block.get("page"), int)
    ]
    return max(pages) - min(pages) + 1 <= max_page_span


def _text_chunk_metadata(
    blocks: Sequence[dict],
    word_counts: Sequence[int],
    token_start: int,
    token_end: int,
) -> dict:
    """Copy source metadata and record the pages contributing to one chunk."""
    included: list[dict] = []
    cursor = 0
    for block, count in zip(blocks, word_counts):
        block_end = cursor + count
        if count and block_end > token_start and cursor < token_end:
            included.append(block)
        cursor = block_end
    contributing = included or list(blocks[:1])
    metadata = _copy_metadata(contributing[0]) if contributing else {}
    pages = sorted(
        {
            block["page"]
            for block in contributing
            if isinstance(block.get("page"), int)
        }
    )
    if pages:
        metadata["page"] = pages[0]
    if len(pages) > 1:
        metadata.update(
            {
                "page_start": pages[0],
                "page_end": pages[-1],
                "pages": pages,
            }
        )
    return metadata


def chunk_structured_blocks(
    blocks: Sequence[dict],
    size: int,
    overlap: int,
    min_text_size: int = 100,
    max_text_page_span: int = 2,
) -> list[dict]:
    """
    Chunk text by section and tables by complete rows.

    Inputs:
        blocks: Ordered common-representation blocks.
        size: Target maximum word-token count per chunk.
        overlap: Word overlap for ordinary text chunks; tables do not split rows.
        min_text_size: Best-effort minimum word target used when deciding
            whether compatible text can continue onto the next page. Zero
            preserves page-local text grouping.
        max_text_page_span: Maximum number of consecutive pages represented by
            one merged text group.

    Returns:
        Chunk dictionaries compatible with all three existing RAG indexes.
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("Require size > 0 and 0 <= overlap < size")
    if min_text_size < 0:
        raise ValueError("min_text_size must be zero or greater")
    if max_text_page_span <= 0:
        raise ValueError("max_text_page_span must be greater than zero")
    effective_min_text_size = min(size, min_text_size)
    chunks: list[dict] = []
    pending: list[dict] = []

    def append_chunk(text: str, source_text: str, metadata: dict) -> None:
        """
        Append one globally numbered chunk with source metadata.

        Inputs:
            text: Token-normalized text used by retrieval indexes.
            source_text: Structure-preserving text supplied to answer context.
            metadata: Source, page, heading, or table metadata.

        Returns:
            None. The enclosing chunk list is updated in place.
        """
        item = {
            "id": len(chunks),
            "text": text,
            "source_text": source_text,
            **metadata,
        }
        chunks.append(item)

    def flush_text() -> None:
        """
        Chunk accumulated non-table blocks sharing one source section.

        Inputs:
            None; pending blocks are captured from the enclosing function.

        Returns:
            None. New chunks are appended and the pending list is cleared.
        """
        nonlocal pending
        if not pending:
            return
        parts = _render_text_parts(pending)
        source_text = "\n\n".join(part for part in parts if part)
        word_counts = [len(tokenize_words(part)) for part in parts]
        step = size - overlap
        for local in chunk_words(source_text, size, overlap):
            token_start = local["id"] * step
            token_end = token_start + len(tokenize_words(local["source_text"]))
            metadata = _text_chunk_metadata(
                pending,
                word_counts,
                token_start,
                token_end,
            )
            metadata["block_type"] = "text"
            append_chunk(local["text"], local["source_text"], metadata)
        pending = []

    def append_table_rows(block: dict) -> None:
        """
        Pack complete rows from one table or column group into chunks.

        Inputs:
            block: Table block whose rows and columns must remain intact.

        Returns:
            None. One or more table chunks are appended to the result.
        """
        rows = block.get("rows", [])
        selected: list[list[str]] = []
        start_row = 0

        def flush_rows(end_row: int) -> None:
            """
            Append the selected rows ending at a zero-based boundary.

            Inputs:
                end_row: Exclusive zero-based row boundary, also equal to the
                    inclusive one-based row number stored as provenance.

            Returns:
                None. A table chunk is appended and selected rows are cleared.
            """
            nonlocal selected
            if not selected:
                return
            table_text = render_block(block, selected)
            metadata = {
                **_copy_metadata(block),
                "block_type": "table",
                "table_id": block.get("table_id", ""),
                "row_start": start_row + 1,
                "row_end": end_row,
                "column_group": block.get("column_group", block.get("headers", [])),
                "table_prefix": "\n".join(
                    part
                    for part in (
                        " > ".join(block.get("heading_path", [])),
                        block.get("caption", ""),
                    )
                    if part
                ),
                "table_row_texts": [
                    render_table_row(block, selected_row) for selected_row in selected
                ],
            }
            append_chunk(" ".join(tokenize_words(table_text)), table_text, metadata)
            selected = []

        for row_index, row in enumerate(rows):
            candidate = selected + [row]
            if selected and _table_word_count(block, candidate) > size:
                flush_rows(row_index)
                start_row = row_index
            selected.append(row)
        flush_rows(len(rows))

    for block in associate_table_headings(blocks):
        if block.get("type") == "table":
            if not block.get("rows"):
                flush_text()
                continue
            while pending and _table_retains_introductory_block(
                pending[-1], block
            ):
                pending.pop()
            flush_text()
            for table_group in _table_column_groups(block, size):
                append_table_rows(table_group)
            continue

        if pending:
            crosses_page = pending[-1].get("page") != block.get("page")
            target_reached = (
                effective_min_text_size == 0
                or _text_group_word_count(pending) >= effective_min_text_size
            )
            if not _compatible_text_block(
                pending,
                block,
                max_text_page_span,
            ) or (crosses_page and target_reached):
                flush_text()
        pending.append(block)
    flush_text()
    return chunks


def build_chunks(
    blocks: Sequence[dict] | str | Path,
    output_path: str | Path | None = None,
    *,
    strategy: str = "words",
    chunk_size: int = 450,
    chunk_overlap: int = 60,
    min_text_chunk_size: int = 100,
    max_text_page_span: int = 2,
) -> list[dict]:
    """
    Build and optionally persist chunks from extracted blocks.

    Inputs:
        blocks: A validated block list or path to ``blocks.json``.
        output_path: Optional destination for a UTF-8 ``chunks.json`` file.
        strategy: Chunking strategy. Currently only ``"words"`` is supported.
        chunk_size: Target maximum word count for one chunk.
        chunk_overlap: Repeated words between ordinary text chunks. Complete
            table rows are never overlapped or split.
        min_text_chunk_size: Best-effort minimum word target for merging
            compatible short text across consecutive pages. Values larger than
            ``chunk_size`` are capped at that maximum; zero disables cross-page
            merging.
        max_text_page_span: Maximum consecutive-page span for one merged text
            group.

    Returns:
        Structure-aware chunk dictionaries in retrieval order.
    """
    if strategy != "words":
        raise ValueError(
            f"Unsupported chunking strategy {strategy!r}; currently use 'words'."
        )
    if isinstance(blocks, (str, Path)):
        source_blocks = load_structured_blocks(Path(blocks))
    else:
        source_blocks = validate_blocks(list(blocks))

    chunks = chunk_structured_blocks(
        source_blocks,
        chunk_size,
        chunk_overlap,
        min_text_chunk_size,
        max_text_page_span,
    )
    if not chunks:
        raise ValueError("The extracted blocks did not produce any chunks.")
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(chunks, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(path)
    return chunks
