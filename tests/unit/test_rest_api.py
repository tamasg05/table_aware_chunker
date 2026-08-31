"""Unit tests for the optional stateless REST adapter."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REST_TEST_DEPENDENCIES = all(
    importlib.util.find_spec(module) is not None
    for module in ("fastapi", "httpx", "multipart")
)


@unittest.skipUnless(
    REST_TEST_DEPENDENCIES,
    "Install the rest and rest-test optional dependencies",
)
class RestApiTests(unittest.TestCase):
    """Exercise HTTP validation and portable extraction responses."""

    @classmethod
    def setUpClass(cls):
        from httpx import ASGITransport, AsyncClient

        module_path = Path(__file__).parents[2] / "rest-wrapper" / "app.py"
        specification = importlib.util.spec_from_file_location(
            "table_aware_chunker_rest_app", module_path
        )
        if specification is None or specification.loader is None:
            raise RuntimeError(f"Cannot load REST application: {module_path}")
        module = importlib.util.module_from_spec(specification)
        sys.modules[specification.name] = module
        specification.loader.exec_module(module)

        cls.transport_class = ASGITransport
        cls.client_class = AsyncClient
        cls.rest_module = module
        cls.app = module.app

    def request(self, method: str, path: str, **options):
        async def send():
            async with self.client_class(
                transport=self.transport_class(app=self.app),
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **options)

        return asyncio.run(send())

    def test_health(self):
        response = self.request("GET", "/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_chunks_endpoint(self):
        response = self.request(
            "POST",
            "/v1/chunks",
            json={
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "one two three four",
                        "source_name": "sample.pdf",
                        "source_url": "",
                        "page": 1,
                        "heading_path": [],
                    }
                ],
                "chunk_size": 3,
                "chunk_overlap": 1,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["chunks"][0]["text"], "one two three")
        self.assertEqual(body["chunks"][1]["text"], "three four")

    def test_chunks_endpoint_rejects_invalid_overlap(self):
        response = self.request(
            "POST",
            "/v1/chunks",
            json={"blocks": [], "chunk_size": 10, "chunk_overlap": 10},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("smaller than", response.json()["detail"])

    def test_extract_endpoint_returns_portable_artifacts(self):
        block = {
            "type": "paragraph",
            "text": "Extracted text",
            "source_name": "sample.pdf",
            "source_url": "",
            "page": 1,
            "heading_path": [],
        }

        def fake_extract(sources, output_directory, **_options):
            self.assertEqual(len(sources), 1)
            corpus_directory = Path(output_directory) / "corpus-id"
            corpus_directory.mkdir(parents=True)
            blocks_path = corpus_directory / "blocks.json"
            corpus_path = corpus_directory / "corpus.txt"
            sources_path = corpus_directory / "sources.json"
            blocks_path.write_text(json.dumps([block]), encoding="utf-8")
            corpus_path.write_text("Extracted text\n", encoding="utf-8")
            sources_path.write_text(
                json.dumps([{"kind": "pdf", "filename": "sample.pdf"}]),
                encoding="utf-8",
            )
            return SimpleNamespace(
                blocks_path=blocks_path,
                corpus_path=corpus_path,
                sources_path=sources_path,
                request_key="request-key",
                content_key="content-key",
                source_count=1,
            )

        with patch.object(
            self.rest_module, "extract_corpus", side_effect=fake_extract
        ):
            response = self.request(
                "POST",
                "/v1/extract",
                files={"files": ("sample.pdf", b"%PDF-sample", "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["corpus_id"], "corpus-id")
        self.assertEqual(body["blocks"], [block])
        self.assertEqual(body["corpus_text"], "Extracted text\n")
        self.assertEqual(body["source_count"], 1)

    def test_extract_endpoint_rejects_missing_sources(self):
        response = self.request("POST", "/v1/extract")

        self.assertEqual(response.status_code, 422)
        self.assertIn("either PDF files or HTML URLs", response.json()["detail"])

    def test_url_extraction_is_disabled_by_default(self):
        with patch.object(self.rest_module, "ALLOW_URL_SOURCES", False):
            response = self.request(
                "POST",
                "/v1/extract",
                data={"urls": "https://example.com/page"},
            )

        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
