"""Unit tests for deterministic model-year option links."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from table_aware_chunker import (
    ASTRA_PROFILE,
    JsonSchemaValidationError,
    ModelYearDataError,
    build_model_year_options_file,
    build_model_year_options_from_blocks,
    link_model_year_options,
    link_model_year_options_file,
    validate_json_schema,
    validate_model_year_links,
)


def _draft_model_year() -> dict:
    return {
        "name": "Example",
        "validFrom": "2026-06-01T00:00:00+02:00",
        "versions": [
            {
                "versionData": {
                    "marketingName": "Edition Hybrid",
                    "priceGross": 10_000_000.0,
                }
            },
            {
                "versionData": {
                    "marketingName": "GS Dízel",
                    "versionCode": "GS_DIESEL",
                    "priceGross": 11_000_000.0,
                }
            },
        ],
        "options": [
            {
                "marketingName": "Heated seats",
                "type": "OPTION",
                "versionStatus": [
                    {"status": "STANDARD", "priceGross": 0.0},
                    {"status": "OPTION", "priceGross": 100_000.0},
                ],
            },
            {
                "marketingName": "Blue",
                "type": "COLOR",
                "versionStatus": [
                    {"status": "OPTION", "priceGross": 50_000.0},
                    {"status": "NOT_AVAILABLE", "priceGross": 0.0},
                ],
            },
        ],
    }


def _representative_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["versions", "options"],
        "properties": {
            "name": {"type": ["string", "null"]},
            "validFrom": {"type": ["string", "null"], "format": "date-time"},
            "versions": {
                "type": "array",
                "items": {"$ref": "#/$defs/version"},
            },
            "options": {
                "type": "array",
                "items": {"$ref": "#/$defs/option"},
            },
        },
        "$defs": {
            "version": {
                "type": "object",
                "required": ["versionData"],
                "properties": {
                    "versionData": {
                        "type": "object",
                        "required": ["versionCode"],
                        "properties": {
                            "versionCode": {"type": "string"},
                            "marketingName": {"type": "string"},
                            "priceGross": {"type": "number"},
                        },
                    },
                },
            },
            "option": {
                "type": "object",
                "required": ["id", "type", "versionStatus"],
                "properties": {
                    "id": {"type": "integer"},
                    "marketingName": {"type": "string"},
                    "type": {"enum": ["OPTION", "COLOR"]},
                    "versionStatus": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["status"],
                            "properties": {
                                "status": {
                                    "enum": [
                                        "STANDARD",
                                        "OPTION",
                                        "NOT_AVAILABLE",
                                    ]
                                },
                                "priceGross": {"type": "number"},
                            },
                        },
                    },
                },
            },
        },
    }


def _astra_blocks() -> list[dict]:
    headers = [
        "Felszereltség",
        "Motor",
        "Váltómű",
        "Listaár",
        "Kedvezmény",
        "Der Opel Nyár kedvezmény",
        "Kedvezményes ár",
    ]
    combustion_rows = []
    for trim, list_price, final_price in (
        ("Edition", "12590000", "9990000"),
        ("GS", "13590000", "10990000"),
        ("Ultimate", "14790000", "12190000"),
    ):
        combustion_rows.extend(
            [
                [
                    f"{trim} Hybrid",
                    "hajtáslánc (107 kW/145 LE)",
                    "6-fokozatú automata",
                    list_price,
                    "1800000",
                    "800000",
                    final_price,
                ],
                [
                    f"{trim} Turbo",
                    "dízel (96 kW / 130 LE)",
                    "8-fokozatú automata",
                    list_price,
                    "1800000",
                    "800000",
                    final_price,
                ],
            ]
        )
    phev_rows = [
        [
            f"{trim} Plug-in",
            "hybrid (144 kW / 195 LE)",
            "7-fokozatú automata",
            list_price,
            "2000000",
            "800000",
            final_price,
        ]
        for trim, list_price, final_price in (
            ("Edition", "16690000", "13890000"),
            ("GS", "17690000", "14890000"),
            ("Ultimate", "18790000", "15990000"),
        )
    ]
    return [
        {
            "type": "table",
            "source_name": "astra1.pdf",
            "page": 3,
            "caption": "Listaárak és kedvezményes árak",
            "headers": headers,
            "rows": combustion_rows,
        },
        {
            "type": "table",
            "page": 4,
            "caption": "Listaárak és kedvezményes árak",
            "headers": headers,
            "rows": phev_rows,
        },
        {
            "type": "paragraph",
            "page": 5,
            "text": "Standard felszereltség Edition GS Ultimate",
        },
        {
            "type": "table",
            "page": 6,
            "caption": "Technológia és infotainment",
            "headers": [
                "Kód",
                "Technológia és infotainment",
                "Leírás",
                "Edition",
                "GS",
                "Ultimate",
            ],
            "rows": [
                [
                    "DZJG9",
                    "Multimedia Navi infotainment csomag",
                    "Navigáció teljes Európa-térképpel Élő navigáció "
                    "Vezeték nélküli frissítések Vezeték nélküli telefontöltő",
                    "220000 Ft",
                    "-",
                    "-",
                ]
            ],
        },
        {
            "type": "paragraph",
            "page": 12,
            "text": "A tartalom 2026. június 1-én rendelkezésre álló "
            "információkon alapul. MY26B",
        },
    ]


class ModelYearLinkTests(unittest.TestCase):
    def test_links_options_without_mutating_input(self):
        draft = _draft_model_year()
        draft["versions"][0]["id"] = 91
        draft["versions"][0]["versionData"]["id"] = 91
        draft["options"][0]["versionStatus"][0]["id"] = 91
        original = deepcopy(draft)

        linked = link_model_year_options(draft)

        self.assertEqual(draft, original)
        self.assertTrue(all("id" not in item for item in linked["versions"]))
        self.assertTrue(
            all("id" not in item["versionData"] for item in linked["versions"])
        )
        self.assertEqual(
            [item["versionData"]["versionCode"] for item in linked["versions"]],
            ["EDITION_HYBRID", "GS_DIESEL"],
        )
        self.assertEqual([item["id"] for item in linked["options"]], [1, 2])
        for option in linked["options"]:
            self.assertTrue(
                all("id" not in status for status in option["versionStatus"])
            )
        validate_model_year_links(linked)

    def test_accepts_explicit_version_codes(self):
        linked = link_model_year_options(
            _draft_model_year(), version_codes=["edition-1", "gs-2"]
        )

        self.assertEqual(
            [item["versionData"]["versionCode"] for item in linked["versions"]],
            ["EDITION_1", "GS_2"],
        )

    def test_preserves_brochure_option_code_and_description(self):
        draft = _draft_model_year()
        draft["options"][0]["optionCode"] = "DZJG9"
        draft["options"][0]["description"] = (
            "Navigáció teljes Európa-térképpel\n"
            "Élő navigáció\n"
            "Vezeték nélküli frissítések\n"
            "Vezeték nélküli telefontöltő"
        )

        linked = link_model_year_options(draft)

        self.assertEqual(linked["options"][0]["optionCode"], "DZJG9")
        self.assertEqual(
            linked["options"][0]["description"],
            draft["options"][0]["description"],
        )

    def test_duplicate_derived_codes_receive_stable_suffix(self):
        draft = _draft_model_year()
        draft["versions"][1]["versionData"] = {
            "marketingName": "Edition Hybrid"
        }

        linked = link_model_year_options(draft)

        self.assertEqual(
            [item["versionData"]["versionCode"] for item in linked["versions"]],
            ["EDITION_HYBRID", "EDITION_HYBRID_2"],
        )

    def test_rejects_incomplete_status_matrix(self):
        draft = _draft_model_year()
        draft["options"][0]["versionStatus"].pop()

        with self.assertRaisesRegex(
            ModelYearDataError, "must contain 2 entries, found 1"
        ):
            link_model_year_options(draft)

    def test_rejects_a_version_id_in_linked_output(self):
        linked = link_model_year_options(_draft_model_year())
        linked["versions"][0]["id"] = 1

        with self.assertRaisesRegex(ModelYearDataError, "must not contain id"):
            validate_model_year_links(linked)

    def test_validates_linked_document_against_schema(self):
        linked = link_model_year_options(_draft_model_year())

        validate_json_schema(linked, _representative_schema())

        linked["options"][0]["versionStatus"][0]["status"] = "UNKNOWN"
        with self.assertRaisesRegex(JsonSchemaValidationError, "enum value"):
            validate_json_schema(linked, _representative_schema())

    def test_file_helper_writes_valid_utf8_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "draft.json"
            target = root / "linked.json"
            schema = root / "schema.json"
            source.write_text(
                json.dumps(_draft_model_year(), ensure_ascii=False), encoding="utf-8"
            )
            schema.write_text(
                json.dumps(_representative_schema()), encoding="utf-8"
            )

            result = link_model_year_options_file(
                source, target, schema_path=schema
            )

            persisted = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(persisted, result)
            self.assertIn("Dízel", target.read_text(encoding="utf-8"))
            self.assertFalse(target.with_name("linked.json.tmp").exists())

    def test_builds_linked_options_from_supported_astra_blocks(self):
        result = build_model_year_options_from_blocks(_astra_blocks())

        self.assertEqual(result["name"], "Astra MY26B")
        self.assertEqual(result["validFrom"], "2026-06-01T00:00:00+02:00")
        self.assertEqual(len(result["versions"]), 9)
        self.assertEqual(
            result["versions"][0]["versionData"]["versionCode"],
            "EDITION_HYBRID_145_AT6",
        )
        navigation = next(
            option
            for option in result["options"]
            if option.get("optionCode") == "DZJG9"
        )
        self.assertEqual(
            navigation["description"],
            "Navigáció teljes Európa-térképpel\n"
            "Élő navigáció\n"
            "Vezeték nélküli frissítések\n"
            "Vezeték nélküli telefontöltő",
        )
        self.assertEqual(
            [status["status"] for status in navigation["versionStatus"]],
            [
                "OPTION",
                "OPTION",
                "NOT_AVAILABLE",
                "NOT_AVAILABLE",
                "NOT_AVAILABLE",
                "NOT_AVAILABLE",
                "OPTION",
                "NOT_AVAILABLE",
                "NOT_AVAILABLE",
            ],
        )

    def test_build_file_uses_saved_blocks_and_validates_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blocks_path = root / "blocks.json"
            output_path = root / "options.json"
            schema_path = root / "schema.json"
            blocks_path.write_text(
                json.dumps(_astra_blocks(), ensure_ascii=False), encoding="utf-8"
            )
            schema_path.write_text(
                json.dumps(
                    {
                        "type": "object",
                        "required": ["versions", "options"],
                    }
                ),
                encoding="utf-8",
            )

            result = build_model_year_options_file(
                blocks_path, output_path, schema_path=schema_path
            )

            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")), result
            )
            self.assertEqual(result["name"], "Astra MY26B")
            self.assertFalse(output_path.with_name("options.json.tmp").exists())

    def test_rejects_an_unsupported_brochure_layout(self):
        with self.assertRaisesRegex(ModelYearDataError, "does not match"):
            build_model_year_options_from_blocks(
                [{"type": "paragraph", "text": "Unrelated brochure"}],
                profile=ASTRA_PROFILE,
            )


if __name__ == "__main__":
    unittest.main()
