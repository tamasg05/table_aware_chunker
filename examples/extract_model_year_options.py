"""Build linked model-year versions and options from ``blocks.json``."""

from __future__ import annotations

import argparse
from pathlib import Path

from table_aware_chunker import ASTRA_PROFILE, build_model_year_options_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build schema-compatible model-year versions and options from "
            "a supported brochure's extracted blocks.json."
        )
    )
    parser.add_argument(
        "blocks_json", type=Path, help="blocks.json created from the brochure"
    )
    parser.add_argument("output_json", type=Path, help="Generated JSON file")
    parser.add_argument(
        "--schema",
        type=Path,
        help="Optional JSON Schema used to validate the generated document",
    )
    parser.add_argument(
        "--profile",
        default="auto",
        choices=("auto", ASTRA_PROFILE),
        help="Brochure layout profile; auto detects supported layouts",
    )
    parser.add_argument(
        "--source-name",
        help=(
            "Optional original brochure filename; normally inferred from "
            "source_name in the blocks"
        ),
    )
    parser.add_argument(
        "--name",
        help="Optional model-year name override, for example 'Astra MY26B'",
    )
    parser.add_argument(
        "--valid-from",
        help=(
            "Optional RFC 3339 validity timestamp override, including timezone"
        ),
    )
    args = parser.parse_args()

    result = build_model_year_options_file(
        args.blocks_json,
        args.output_json,
        schema_path=args.schema,
        source_name=args.source_name,
        profile=args.profile,
        name=args.name,
        valid_from=args.valid_from,
    )
    print(
        f"Extracted {len(result['versions'])} versions and "
        f"{len(result['options'])} options: {args.output_json}"
    )


if __name__ == "__main__":
    main()
