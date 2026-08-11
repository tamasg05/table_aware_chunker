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

    def test_reconstruction_converts_repeated_label_value_cards(self):
        table = SimpleNamespace(
            bbox=(0, 0, 300, 100),
            rows=[
                SimpleNamespace(cells=[(0, 0, 300, 10), None, None]),
                SimpleNamespace(
                    cells=[
                        (0, 20, 100, 50),
                        (100, 20, 200, 50),
                        (200, 20, 300, 50),
                    ]
                ),
                SimpleNamespace(
                    cells=[(0, 60, 150, 90), (150, 60, 300, 90), None]
                ),
            ],
        )
        words = [
            word("Available", 5, 1, 40, 8),
            word("options", 42, 1, 75, 8),
            word("Alpha", 5, 25, 35, 32),
            word("Beta", 105, 25, 135, 32),
            word("Gamma", 205, 25, 240, 32),
            word("10", 70, 38, 85, 45),
            word("20", 170, 38, 185, 45),
            word("30", 270, 38, 285, 45),
            word("Delta", 5, 65, 35, 72),
            word("Epsilon", 155, 65, 195, 72),
            word("40", 120, 78, 135, 85),
            word("50", 270, 78, 285, 85),
        ]

        headers, rows, sections, _ = _reconstruct_pdf_table(table, words)

        self.assertEqual(headers, ["Label", "Value"])
        self.assertEqual(
            rows,
            [
                ["Alpha", "10"],
                ["Beta", "20"],
                ["Gamma", "30"],
                ["Delta", "40"],
                ["Epsilon", "50"],
            ],
        )
        self.assertEqual(sections, [(0.0, "Available options")])

    def test_reconstruction_rejects_multiline_prose_as_external_headers(self):
        table = SimpleNamespace(
            bbox=(0, 100, 400, 140),
            rows=[
                SimpleNamespace(
                    cells=[
                        (0, 100, 100, 120),
                        (100, 100, 200, 120),
                        (200, 100, 300, 120),
                        (300, 100, 400, 120),
                    ]
                ),
                SimpleNamespace(
                    cells=[
                        (0, 120, 100, 140),
                        (100, 120, 200, 140),
                        (200, 120, 300, 140),
                        (300, 120, 400, 140),
                    ]
                ),
            ],
        )
        prose_words = []
        for line_number, top in enumerate((60, 70, 80), start=1):
            for column in range(4):
                left = column * 100 + 5
                prose_words.extend(
                    [
                        word(f"prose{line_number}-{column}a", left, top, left + 35, top + 7),
                        word(
                            f"prose{line_number}-{column}b",
                            left + 38,
                            top,
                            left + 75,
                            top + 7,
                        ),
                    ]
                )
        table_words = [
            word("Prices", 5, 105, 45, 112),
            word("Basic", 105, 105, 145, 112),
            word("Duo", 205, 105, 245, 112),
            word("Premium", 305, 105, 355, 112),
            word("Device", 5, 125, 45, 132),
            word("100", 105, 125, 145, 132),
            word("200", 205, 125, 245, 132),
            word("300", 305, 125, 345, 132),
        ]
        reconstruction_info: dict[str, bool] = {}

        headers, rows, _, consumed = _reconstruct_pdf_table(
            table,
            [*prose_words, *table_words],
            reconstruction_info=reconstruction_info,
        )

        self.assertEqual(headers, ["Prices", "Basic", "Duo", "Premium"])
        self.assertEqual(rows, [["Device", "100", "200", "300"]])
        self.assertTrue(reconstruction_info["rejected_prose_headers"])
        self.assertFalse(any(key[0].startswith("prose") for key in consumed))


if __name__ == "__main__":
    unittest.main()
