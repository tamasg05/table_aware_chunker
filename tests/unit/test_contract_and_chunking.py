"""Fast tests for the public block contract and chunking API."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from table_aware_chunker import (
    BLOCKS_SCHEMA_VERSION,
    CHUNKS_SCHEMA_VERSION,
    BlockValidationError,
    build_chunks,
    chunk_words,
    extract_corpus,
    load_structured_blocks,
    make_table_block,
    make_text_block,
    render_block,
    save_structured_corpus,
    tokenize_words,
    validate_blocks,
)


class ContractAndChunkingTests(unittest.TestCase):
    def test_schema_versions_are_independent(self):
        self.assertEqual(BLOCKS_SCHEMA_VERSION, "1.0")
        self.assertEqual(CHUNKS_SCHEMA_VERSION, "1.1")

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

    def test_introductory_heading_is_attached_and_not_chunked_separately(self):
        heading = make_text_block(
            "Operating limits",
            "specification.pdf",
            page=3,
            heading_path=["Electrical system"],
            block_type="heading",
        )
        table = make_table_block(
            ["Parameter", "Value"],
            [["Voltage", "3.6 V"]],
            "specification.pdf",
            table_id="table-1",
            page=3,
        )

        with tempfile.TemporaryDirectory() as temporary:
            saved = save_structured_corpus(
                [heading, table],
                [{"kind": "pdf", "filename": "specification.pdf"}],
                Path(temporary),
                request_key="heading-table-test",
            )
            saved_blocks = load_structured_blocks(saved.blocks_path)
            self.assertEqual(
                saved_blocks[1]["heading_path"],
                ["Electrical system", "Operating limits"],
            )

            chunks = build_chunks(
                saved.blocks_path,
                strategy="words",
                chunk_size=30,
                chunk_overlap=0,
            )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["block_type"], "table")
        self.assertTrue(
            chunks[0]["source_text"].startswith(
                "Electrical system > Operating limits\n"
            )
        )
        self.assertNotEqual(chunks[0]["source_text"], "Operating limits")

    def test_table_caption_suppresses_only_its_duplicate_preceding_block(self):
        overview = make_text_block(
            "This section explains the available configurations.",
            "specification.pdf",
            page=1,
        )
        caption_block = make_text_block(
            "Configuration matrix",
            "specification.pdf",
            page=1,
        )
        table = make_table_block(
            ["Option", "Status"],
            [["Feature A", "Available"]],
            "specification.pdf",
            table_id="table-1",
            caption="Configuration matrix",
            page=1,
        )

        chunks = build_chunks(
            [overview, caption_block, table],
            strategy="words",
            chunk_size=30,
            chunk_overlap=0,
        )

        self.assertEqual(
            [chunk["block_type"] for chunk in chunks], ["text", "table"]
        )
        self.assertIn("available configurations", chunks[0]["source_text"])
        self.assertEqual(chunks[1]["source_text"].count("Configuration matrix"), 1)

    def test_empty_table_does_not_suppress_its_preceding_heading(self):
        heading = make_text_block(
            "Empty results",
            "specification.pdf",
            page=1,
            block_type="heading",
        )
        table = make_table_block(
            ["Name", "Value"],
            [],
            "specification.pdf",
            table_id="table-1",
            page=1,
        )

        chunks = build_chunks(
            [heading, table],
            strategy="words",
            chunk_size=30,
            chunk_overlap=0,
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["block_type"], "text")
        self.assertEqual(chunks[0]["source_text"], "Empty results")

    def test_short_compatible_text_merges_across_two_pages_before_a_table(self):
        blocks = [
            make_text_block(
                "Charging options",
                "brochure.pdf",
                page=4,
                block_type="heading",
            ),
            make_text_block(
                "Planning and installation details.",
                "brochure.pdf",
                page=4,
            ),
            make_text_block(
                "Maintenance support continues on the following page.",
                "brochure.pdf",
                page=5,
            ),
            make_table_block(
                ["Device", "Price"],
                [["Wallbox", "100"]],
                "brochure.pdf",
                table_id="table-1",
                page=5,
            ),
        ]

        chunks = build_chunks(
            blocks,
            chunk_size=30,
            chunk_overlap=0,
            min_text_chunk_size=20,
            max_text_page_span=2,
        )

        self.assertEqual([chunk["block_type"] for chunk in chunks], ["text", "table"])
        self.assertIn("Charging options", chunks[0]["source_text"])
        self.assertIn("Planning and installation", chunks[0]["source_text"])
        self.assertIn("Maintenance support", chunks[0]["source_text"])
        self.assertEqual(chunks[0]["page"], 4)
        self.assertEqual(chunks[0]["page_start"], 4)
        self.assertEqual(chunks[0]["page_end"], 5)
        self.assertEqual(chunks[0]["pages"], [4, 5])

    def test_table_remains_a_hard_boundary_between_short_text_blocks(self):
        blocks = [
            make_text_block("Text before the table.", "brochure.pdf", page=4),
            make_table_block(
                ["Device", "Price"],
                [["Wallbox", "100"]],
                "brochure.pdf",
                table_id="table-1",
                page=4,
            ),
            make_text_block("Text after the table.", "brochure.pdf", page=5),
        ]

        chunks = build_chunks(
            blocks,
            chunk_size=30,
            chunk_overlap=0,
            min_text_chunk_size=20,
            max_text_page_span=2,
        )

        self.assertEqual(
            [chunk["block_type"] for chunk in chunks],
            ["text", "table", "text"],
        )
        self.assertNotIn("after", chunks[0]["source_text"])
        self.assertNotIn("before", chunks[2]["source_text"])

    def test_heading_is_attached_forward_without_repeating_its_path(self):
        blocks = [
            make_text_block(
                "Charging options",
                "brochure.pdf",
                page=1,
                block_type="heading",
            ),
            make_text_block(
                "Choose the appropriate charger.",
                "brochure.pdf",
                page=1,
                heading_path=["Charging options"],
            ),
        ]

        chunks = build_chunks(
            blocks,
            chunk_size=30,
            chunk_overlap=0,
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["source_text"].count("Charging options"), 1)
        self.assertTrue(chunks[0]["source_text"].endswith("appropriate charger."))

    def test_minimum_text_target_does_not_cross_more_than_the_page_limit(self):
        blocks = [
            make_text_block("Page one text.", "brochure.pdf", page=1),
            make_text_block("Page two text.", "brochure.pdf", page=2),
            make_text_block("Page three text.", "brochure.pdf", page=3),
        ]

        chunks = build_chunks(
            blocks,
            chunk_size=30,
            chunk_overlap=0,
            min_text_chunk_size=20,
            max_text_page_span=2,
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["pages"], [1, 2])
        self.assertEqual(chunks[1]["page"], 3)
        self.assertNotIn("pages", chunks[1])

    def test_invalid_text_consolidation_options_are_rejected(self):
        block = make_text_block("Text", "brochure.pdf", page=1)
        with self.assertRaisesRegex(ValueError, "min_text_size"):
            build_chunks([block], min_text_chunk_size=-1)
        with self.assertRaisesRegex(ValueError, "max_text_page_span"):
            build_chunks([block], max_text_page_span=0)

    def test_unknown_chunking_strategy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported chunking strategy"):
            build_chunks([], strategy="tokens")


if __name__ == "__main__":
    unittest.main()
