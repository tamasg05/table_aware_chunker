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
    )

    print(f"Extracted corpus: {corpus.corpus_path}")
    print(f"Structured blocks: {corpus.blocks_path}")
    print(f"Created {len(chunks)} chunks: {chunks_path}")


if __name__ == "__main__":
    main()
