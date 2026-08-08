"""Unit tests for HTML extraction and persistence."""

from __future__ import annotations

import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from data_extraction import build_chunks
from data_extraction.html_extractor import (
    WebPage,
    extract_html_blocks,
    extract_html_text,
    fetch_html_pages,
    parse_url_list,
    save_web_corpus,
    url_request_key,
)


class HtmlExtractorTests(unittest.TestCase):
    def test_page_download_uses_final_url_and_visible_text(self):
        class FakeResponse:
            def __init__(self):
                self.headers = Message()
                self.headers["Content-Type"] = "text/html; charset=utf-8"

            def __enter__(self):
                return self

            def __exit__(self, exception_type, exception, traceback):
                return False

            def geturl(self):
                return "https://example.org/final"

            def read(self, limit):
                return b"<html><body><main><p>Downloaded text.</p></main></body></html>"

        with patch(
            "data_extraction.html_extractor.urllib.request.urlopen",
            return_value=FakeResponse(),
        ):
            pages = fetch_html_pages(
                ["https://example.org/start"],
                timeout_seconds=1,
                max_page_bytes=1_000,
            )
        self.assertEqual(pages[0].final_url, "https://example.org/final")
        self.assertEqual(pages[0].text, "Downloaded text.")

    def test_url_list_validation_and_identity(self):
        urls = parse_url_list(
            "https://example.org/one\n\nhttps://example.org/two\n"
            "https://example.org/one",
            max_pages=2,
        )
        self.assertEqual(
            urls, ["https://example.org/one", "https://example.org/two"]
        )
        self.assertEqual(url_request_key(urls), url_request_key(list(urls)))
        with self.assertRaises(ValueError):
            parse_url_list("file:///tmp/private.txt", max_pages=2)
        with self.assertRaises(ValueError):
            parse_url_list("https://user:secret@example.org", max_pages=2)

    def test_visible_html_is_saved_as_structured_corpus(self):
        title, text = extract_html_text(
            """
            <html><head><title> Example article </title>
            <style>hidden style</style></head><body>
            <nav>navigation</nav><main><h1>Visible heading</h1>
            <p>First <strong>useful</strong> paragraph.</p>
            <script>hidden script</script></main></body></html>
            """
        )
        self.assertEqual(title, "Example article")
        self.assertIn("Visible heading", text)
        self.assertIn("First useful paragraph.", text)
        self.assertNotIn("hidden", text)
        self.assertNotIn("navigation", text)

        pages = [
            WebPage(
                requested_url="https://example.org/article",
                final_url="https://example.org/article",
                title=title,
                text=text,
            )
        ]
        with tempfile.TemporaryDirectory() as temporary:
            saved = save_web_corpus(pages, Path(temporary))
            sources = json.loads(saved.sources_path.read_text(encoding="utf-8"))
            self.assertEqual(sources[0]["requested_url"], pages[0].requested_url)
            self.assertTrue(saved.blocks_path.exists())
            self.assertEqual(saved.source_count, 1)

    def test_html_rowspan_and_colspan_form_interpretable_headers(self):
        _, blocks = extract_html_blocks(
            """
            <html><body><main><h1>Electrical limits</h1>
            <table><caption>Operating range</caption><thead>
            <tr><th rowspan="2">Parameter</th><th colspan="2">Limits</th></tr>
            <tr><th>Minimum</th><th>Maximum</th></tr></thead><tbody>
            <tr><td>Voltage</td><td>3.0 V</td><td>3.6 V</td></tr>
            <tr><td>Current</td><td>1 A</td><td>2 A</td></tr>
            </tbody></table></main></body></html>
            """,
            source_name="specification",
            source_url="https://example.org/specification",
        )
        table = next(block for block in blocks if block["type"] == "table")
        self.assertEqual(
            table["headers"],
            ["Parameter", "Limits > Minimum", "Limits > Maximum"],
        )
        chunks = build_chunks(
            [table], strategy="words", chunk_size=30, chunk_overlap=0
        )
        self.assertIn("Parameter = Voltage", chunks[0]["source_text"])
        self.assertIn("Limits > Maximum = 3.6 V", chunks[0]["source_text"])


if __name__ == "__main__":
    unittest.main()
