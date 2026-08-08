"""Fast tests for the public block contract and chunking API."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_extraction import (
    BLOCKS_SCHEMA_VERSION,
    CHUNKS_SCHEMA_VERSION,
    BlockValidationError,
    build_chunks,
    chunk_words,
    extract_corpus,
    render_block,
    tokenize_words,
    validate_blocks,
)


class ContractAndChunkingTests(unittest.TestCase):
    def test_schema_versions_are_independent(self):
        self.assertEqual(BLOCKS_SCHEMA_VERSION, "1.0")
        self.assertEqual(CHUNKS_SCHEMA_VERSION, "1.0")

    def test_validation_rejects_a_ragged_table(self):
        with self.assertRaisesRegex(BlockValidationError, "must have 2 cells"):
            validate_blocks(
                [
                    {
                        "type": "table",
                        "source_name": "test.pdf",
                        "page": 1,
                        "heading_path": [],
                        "table_id": "table-1",
                        "headers": ["Name", "Price"],
                        "rows": [["A8"]],
                    }
                ]
            )

    def test_extract_corpus_requires_a_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "Provide at least one"):
                extract_corpus(None, temporary)

    def test_word_tokenizer_removes_surrounding_punctuation(self):
        self.assertEqual(
            tokenize_words("Hello, World! Isn't this Dr. Watson?"),
            ["Hello", "World", "Isn't", "this", "Dr", "Watson"],
        )
        chunks = chunk_words("Hello, World!", size=2, overlap=0)
        self.assertEqual(chunks[0]["text"], "Hello World")
        self.assertEqual(chunks[0]["source_text"], "Hello, World!")

    def test_table_rendering_and_chunk_output_keep_column_meaning(self):
        table = {
            "type": "table",
            "source_name": "prices.pdf",
            "source_url": "",
            "page": 2,
            "heading_path": ["Models"],
            "table_id": "table-1",
            "headers": ["Model", "Power", "Price"],
            "rows": [["A8 55 TFSI", "340 LE", "41256010 HUF"]],
        }
        rendered = render_block(table)
        self.assertIn("Model = A8 55 TFSI", rendered)
        self.assertIn("Price = 41256010 HUF", rendered)

        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "chunks.json"
            chunks = build_chunks(
                [table],
                output_path,
                strategy="words",
                chunk_size=30,
                chunk_overlap=0,
            )
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")), chunks
            )
            self.assertEqual(chunks[0]["block_type"], "table")
            self.assertIn("Power = 340 LE", chunks[0]["source_text"])

    def test_unknown_chunking_strategy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported chunking strategy"):
            build_chunks([], strategy="tokens")


if __name__ == "__main__":
    unittest.main()

