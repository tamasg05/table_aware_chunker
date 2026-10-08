"""Create positional version links in an extracted model-year JSON file."""

from __future__ import annotations

import argparse
from pathlib import Path

from table_aware_chunker import link_model_year_options_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Assign option identifiers and positional version links to an "
            "already extracted model-year options JSON document."
        )
    )
    parser.add_argument("input_json", type=Path, help="Draft model-year JSON file")
    parser.add_argument("output_json", type=Path, help="Linked output JSON file")
    parser.add_argument(
        "--schema",
        type=Path,
        help="Optional JSON Schema used to validate the linked output",
    )
    parser.add_argument(
        "--version-code",
        action="append",
        dest="version_codes",
        help=(
            "Explicit version code in version order; repeat once per version. "
            "Codes otherwise come from existing values or marketing names."
        ),
    )
    args = parser.parse_args()

    linked = link_model_year_options_file(
        args.input_json,
        args.output_json,
        schema_path=args.schema,
        version_codes=args.version_codes,
    )
    print(
        f"Linked {len(linked['options'])} options to "
        f"{len(linked['versions'])} versions: {args.output_json}"
    )


if __name__ == "__main__":
    main()
