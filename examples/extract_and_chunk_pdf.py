"""Extract one PDF and create structure-aware chunks."""

from __future__ import annotations

import argparse
from pathlib import Path

from table_aware_chunker import build_chunks, extract_corpus


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract a PDF and save structure-aware chunks."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        type=Path,
        default=Path("tests/artifacts/Q8.pdf"),
        help="Path to the PDF document (default: tests/artifacts/Q8.pdf)",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("example_output"),
        help="Directory for generated corpus files (default: example_output)",
    )
    parser.add_argument(
        "--min-text-chunk-size",
        type=int,
        default=100,
        help=(
            "Best-effort minimum word target for compatible text chunks "
            "(default: 100; use 0 to keep text page-local)"
        ),
    )
    parser.add_argument(
        "--max-text-page-span",
        type=int,
        default=2,
        help="Maximum consecutive-page span for merged text (default: 2)",
    )
    args = parser.parse_args()

    corpus = extract_corpus(
        args.pdf,
        output_directory=args.output_directory,
    )
    chunks_path = corpus.blocks_path.with_name("chunks.json")
    chunks = build_chunks(
        corpus.blocks_path,
        output_path=chunks_path,
        strategy="words",
        chunk_size=450,
        chunk_overlap=60,
        min_text_chunk_size=args.min_text_chunk_size,
        max_text_page_span=args.max_text_page_span,
    )

    print(f"Extracted corpus: {corpus.corpus_path}")
    print(f"Structured blocks: {corpus.blocks_path}")
    print(f"Created {len(chunks)} chunks: {chunks_path}")


if __name__ == "__main__":
    main()
