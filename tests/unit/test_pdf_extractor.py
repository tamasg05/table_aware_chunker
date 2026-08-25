"""Unit tests for PDF input validation and geometric table reconstruction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from table_aware_chunker.pdf_extractor import (
    _clean_pdf_table,
    _group_pdf_text_lines,
    _positioned_words_text,
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
    def test_reconstruction_removes_empty_internal_spacer_columns(self):
        boundaries = (0, 20, 24, 44, 48, 68, 72, 92)
        table = SimpleNamespace(
            bbox=(0, 0, 92, 20),
            rows=[
                SimpleNamespace(
                    cells=[
                        (boundaries[index], top, boundaries[index + 1], bottom)
                        for index in range(7)
                    ]
                )
                for top, bottom in ((0, 10), (10, 20))
            ],
        )
        words = [
            word("Motor", 2, 1, 15, 8),
            word("YOU", 27, 1, 38, 8),
            word("PLUS", 51, 1, 64, 8),
            word("MAX", 75, 1, 87, 8),
            word("Petrol", 2, 11, 16, 18),
            word("100", 27, 11, 38, 18),
            word("200", 51, 11, 62, 18),
            word("300", 75, 11, 86, 18),
        ]
        reconstruction_info: dict[str, object] = {}

        headers, rows, sections, _ = _reconstruct_pdf_table(
            table,
            words,
            reconstruction_info=reconstruction_info,
        )

        self.assertEqual(headers, ["Motor", "YOU", "PLUS", "MAX"])
        self.assertEqual(rows, [["Petrol", "100", "200", "300"]])
        self.assertEqual(sections, [])
        self.assertTrue(reconstruction_info["removed_empty_columns"])
        self.assertTrue(reconstruction_info["promoted_internal_header"])

    def test_reconstruction_attaches_labeled_multi_column_section(self):
        boundaries = (0, 30, 35, 65, 70, 100)
        table = SimpleNamespace(
            bbox=(0, 0, 100, 40),
            rows=[
                SimpleNamespace(
                    cells=[
                        (boundaries[index], 0, boundaries[index + 1], 10)
                        for index in range(5)
                    ]
                ),
                SimpleNamespace(cells=[(0, 10, 100, 40), None, None, None, None]),
            ],
        )
        words = [
            word("YOU", 3, 1, 15, 8),
            word("PLUS", 40, 1, 55, 8),
            word("MAX", 75, 1, 88, 8),
            word("You-1", 3, 12, 15, 18),
            word("Plus-1", 40, 12, 54, 18),
            word("Max-1", 75, 12, 88, 18),
            word("You-2", 3, 22, 15, 28),
            word("Plus-2", 40, 22, 54, 28),
            word("Max-2", 75, 22, 88, 28),
        ]
        reconstruction_info: dict[str, object] = {}

        headers, rows, sections, _ = _reconstruct_pdf_table(
            table,
            words,
            reconstruction_info=reconstruction_info,
        )

        self.assertEqual(headers, ["YOU", "PLUS", "MAX"])
        self.assertEqual(
            rows,
            [["You-1 You-2", "Plus-1 Plus-2", "Max-1 Max-2"]],
        )
        self.assertEqual(sections, [])
        self.assertTrue(reconstruction_info["attached_multi_column_sections"])

    def test_three_or_more_text_columns_are_read_left_to_right(self):
        words = [
            word("First-1", 0, 0, 12, 8),
            word("Second-1", 30, 0, 44, 8),
            word("Third-1", 60, 0, 72, 8),
            word("Fourth-1", 90, 0, 104, 8),
            word("First-2", 0, 10, 12, 18),
            word("Second-2", 30, 10, 44, 18),
            word("Third-2", 60, 10, 72, 18),
            word("Fourth-2", 90, 10, 104, 18),
            word("First-3", 0, 20, 12, 28),
            word("Second-3", 30, 20, 44, 28),
            word("Third-3", 60, 20, 72, 28),
        ]

        self.assertEqual(
            [text for _, text in _group_pdf_text_lines(words)],
            [
                "First-1 First-2 First-3",
                "Second-1 Second-2 Second-3",
                "Third-1 Third-2 Third-3",
                "Fourth-1 Fourth-2",
            ],
        )

    def test_side_by_side_text_columns_are_read_one_column_at_a_time(self):
        words = [
            word("Left", 0, 0, 12, 8),
            word("one", 14, 0, 25, 8),
            word("Right", 35, 0, 48, 8),
            word("one", 50, 0, 61, 8),
            word("Left", 0, 10, 12, 18),
            word("two", 14, 10, 25, 18),
            word("Right", 35, 10, 48, 18),
            word("two", 50, 10, 61, 18),
        ]

        self.assertEqual(
            [text for _, text in _group_pdf_text_lines(words)],
            ["Left one Left two", "Right one Right two"],
        )

    def test_close_text_columns_use_repeated_gutter_not_fixed_page_width(self):
        words = [
            word("Alpha", 0, 0, 12, 8),
            word("first", 14, 0, 25, 8),
            word("Beta", 33, 0, 44, 8),
            word("first", 46, 0, 57, 8),
            word("Alpha", 0, 10, 12, 18),
            word("second", 14, 10, 27, 18),
            word("Beta", 35, 10, 46, 18),
            word("second", 48, 10, 61, 18),
        ]

        self.assertEqual(
            [text for _, text in _group_pdf_text_lines(words)],
            ["Alpha first Alpha second", "Beta first Beta second"],
        )

    def test_single_full_width_lines_are_not_split_into_columns(self):
        words = [
            word("A", 0, 0, 4, 8),
            word("single", 6, 0, 18, 8),
            word("line", 20, 0, 28, 8),
            word("Another", 0, 30, 14, 38),
            word("ordinary", 16, 30, 32, 38),
            word("line", 34, 30, 42, 38),
        ]

        self.assertEqual(
            [text for _, text in _group_pdf_text_lines(words)],
            ["A single line", "Another ordinary line"],
        )

    def test_wrapped_lines_form_one_paragraph_and_repair_fragments(self):
        words = [
            word("A", 0, 0, 4, 8),
            word("complete", 6, 0, 20, 8),
            word("para", 22, 0, 31, 8),
            word("graph", 31.2, 0, 42, 8),
            word("contin-", 0, 10, 14, 18),
            word("ues", 0, 20, 7, 28),
            word("here.", 9, 20, 18, 28),
        ]

        self.assertEqual(
            [text for _, text in _group_pdf_text_lines(words)],
            ["A complete paragraph continues here."],
        )

    def test_grouped_prices_are_compacted_but_alphanumeric_specs_are_not(self):
        self.assertEqual(
            _positioned_words_text(
                [
                    word("41", 0, 0, 5, 5),
                    word("256", 6, 0, 13, 5),
                    word("010", 14, 0, 21, 5),
                    word("Ft", 22, 0, 27, 5),
                ]
            ),
            "41256010 Ft",
        )
        self.assertEqual(
            _positioned_words_text(
                [
                    word("R21", 0, 0, 7, 5),
                    word("113Y", 8, 0, 17, 5),
                    word("xl", 18, 0, 22, 5),
                ]
            ),
            "R21 113Y xl",
        )

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

    def test_reconstruction_recovers_column_between_side_by_side_tables(self):
        table = SimpleNamespace(
            bbox=(70, 10, 100, 30),
            rows=[
                SimpleNamespace(cells=[(70, 10, 85, 20), (85, 10, 100, 20)]),
                SimpleNamespace(cells=[(70, 20, 85, 30), (85, 20, 100, 30)]),
            ],
        )
        words = [
            word("Neighbor", 1, 12, 35, 18),
            word("Description", 45, 1, 67, 8),
            word("A", 72, 1, 80, 8),
            word("B", 87, 1, 95, 8),
            word("First item", 45, 12, 67, 18),
            word("yes", 72, 12, 80, 18),
            word("no", 87, 12, 95, 18),
            word("Second item", 45, 22, 67, 28),
            word("no", 72, 22, 80, 28),
            word("yes", 87, 22, 95, 28),
        ]

        headers, rows, _, consumed = _reconstruct_pdf_table(
            table,
            words,
            table_boxes=[(0, 10, 40, 30), tuple(table.bbox)],
        )

        self.assertEqual(headers, ["Description", "A", "B"])
        self.assertEqual(
            rows,
            [
                ["First item", "yes", "no"],
                ["Second item", "no", "yes"],
            ],
        )
        self.assertFalse(any(key[0] == "Neighbor" for key in consumed))

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
        reconstruction_info: dict[str, object] = {}

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
