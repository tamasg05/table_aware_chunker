"""Unit tests for PDF input validation and geometric table reconstruction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from table_aware_chunker.pdf_extractor import (
    _clean_pdf_table,
    _reconstruct_pdf_table,
    _split_physical_table_row,
    parse_pdf_paths,
    pdf_request_key,
    prepare_pdf_corpus,
)


def word(text, x0, top, x1, bottom):
    """Create one positioned word using the fields supplied by pdfplumber."""
    return {
        "text": text,
        "x0": x0,
        "top": top,
        "x1": x1,
        "bottom": bottom,
        "upright": True,
    }


class PdfExtractorTests(unittest.TestCase):
    def test_table_cleanup_and_content_based_pdf_identity(self):
        headers, rows = _clean_pdf_table(
            [
                ["Parameter", "Minimum", "Maximum"],
                ["Voltage", "3.0 V", "3.6 V"],
                ["Current", "1 A", "2 A"],
            ]
        )
        self.assertEqual(headers, ["Parameter", "Minimum", "Maximum"])
        self.assertEqual(rows[0], ["Voltage", "3.0 V", "3.6 V"])

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "specification.pdf"
            path.write_bytes(b"%PDF-minimal-test")
            parsed = parse_pdf_paths([str(path)], max_files=2, max_file_bytes=100)
            self.assertEqual(parsed, [path.resolve()])

            copy = Path(temporary) / "renamed.pdf"
            copy.write_bytes(path.read_bytes())
            self.assertEqual(pdf_request_key(parsed), pdf_request_key([copy]))

    def test_materialized_pdf_request_is_reused(self):
        block = {
            "type": "paragraph",
            "text": "Reusable PDF text",
            "source_name": "specification.pdf",
            "source_url": "",
            "page": 1,
            "heading_path": [],
        }
        source = {
            "kind": "pdf",
            "filename": "specification.pdf",
            "bytes": 17,
            "pages": 1,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uploaded = root / "specification.pdf"
            uploaded.write_bytes(b"%PDF-reusable-test")
            corpus_root = root / "corpora"
            with patch(
                "table_aware_chunker.pdf_extractor.extract_pdf_blocks",
                return_value=([block], source),
            ) as extract:
                first = prepare_pdf_corpus(
                    [uploaded], corpus_root, max_files=2, max_file_bytes=100
                )
                second = prepare_pdf_corpus(
                    [uploaded], corpus_root, max_files=2, max_file_bytes=100
                )

            self.assertEqual(extract.call_count, 1)
            self.assertEqual(first.blocks_path, second.blocks_path)

    def test_multiple_price_baselines_become_logical_rows(self):
        cell_words = [
            [word("Dinamica", 0, 10, 9, 18)],
            [word("PL4", 10, 10, 19, 18)],
            [word("–", 20, 10, 29, 18), word("o", 20, 30, 29, 38)],
            [
                word("721", 30, 10, 39, 18),
                word("360", 40, 10, 49, 18),
                word("1", 30, 30, 34, 38),
                word("000", 35, 30, 44, 38),
                word("760", 45, 30, 54, 38),
            ],
        ]

        self.assertEqual(
            _split_physical_table_row(cell_words),
            [
                ["Dinamica", "PL4", "–", "721360"],
                ["Dinamica", "PL4", "o", "1000760"],
            ],
        )

    def test_reconstruction_does_not_cross_a_side_by_side_table(self):
        table = SimpleNamespace(
            bbox=(50, 10, 100, 20),
            rows=[SimpleNamespace(cells=[None, (75, 10, 100, 20)])],
        )
        words = [
            word("Other-table", 1, 12, 40, 18),
            word("Name", 51, 1, 70, 8),
            word("Price", 76, 1, 95, 8),
            word("Wheel", 51, 12, 70, 18),
            word("100", 76, 12, 95, 18),
        ]

        headers, rows, _, consumed = _reconstruct_pdf_table(
            table,
            words,
            table_boxes=[(0, 10, 45, 20), tuple(table.bbox)],
        )
        self.assertEqual(headers, ["Name", "Price"])
        self.assertEqual(rows, [["Wheel", "100"]])
        self.assertFalse(any(key[0] == "Other-table" for key in consumed))

    def test_reconstruction_restores_missing_edge_column_and_headers(self):
        table = SimpleNamespace(
            bbox=(10, 10, 30, 30),
            rows=[
                SimpleNamespace(
                    cells=[None, (10, 10, 20, 20), (20, 10, 30, 20)]
                ),
                SimpleNamespace(cells=[(10, 20, 30, 30), None, None]),
            ],
        )
        words = [
            word("Model", 1, 1, 9, 8),
            word("Code", 11, 1, 19, 8),
            word("Price", 21, 1, 29, 8),
            word("A8 55 TFSI", 1, 12, 9, 18),
            word("4NC0DA24", 11, 12, 19, 18),
            word("41 256 010", 21, 12, 29, 18),
            word("Hybrid", 1, 22, 9, 28),
        ]

        headers, rows, sections, consumed = _reconstruct_pdf_table(table, words)
        self.assertEqual(headers, ["Model", "Code", "Price"])
        self.assertEqual(rows, [["A8 55 TFSI", "4NC0DA24", "41256010"]])
        self.assertEqual(sections, [(20.0, "Hybrid")])
        self.assertEqual(len(consumed), len(words))

    def test_reconstruction_infers_multiple_unruled_leading_columns(self):
        table = SimpleNamespace(
            bbox=(60, 0, 90, 20),
            rows=[
                SimpleNamespace(cells=[(60, 0, 80, 10)]),
                SimpleNamespace(cells=[(60, 10, 80, 20)]),
            ],
        )
        words = [
            word("Model", 1, 1, 9, 8),
            word("Version", 30, 1, 40, 8),
            word("Price", 65, 1, 75, 8),
            word("Astra", 1, 11, 9, 18),
            word("GS", 30, 11, 40, 18),
            word("100", 65, 11, 75, 18),
        ]

        headers, rows, sections, _ = _reconstruct_pdf_table(table, words)
        self.assertEqual(headers, ["Model", "Version", "Price"])
        self.assertEqual(rows, [["Astra", "GS", "100"]])
        self.assertEqual(sections, [])


if __name__ == "__main__":
    unittest.main()
