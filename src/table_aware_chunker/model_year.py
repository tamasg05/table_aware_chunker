"""Create consistently linked model-year option documents."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


class ModelYearDataError(ValueError):
    """Raised when model-year versions and option statuses cannot be linked."""


class JsonSchemaValidationError(ValueError):
    """Raised when a generated document does not conform to its JSON Schema."""


def _object(value: object, path: str) -> dict[str, Any]:
    """Return one JSON object or raise a path-specific validation error."""
    if not isinstance(value, dict):
        raise ModelYearDataError(f"{path} must be a JSON object")
    return value


def _array(value: object, path: str) -> list[Any]:
    """Return one JSON array or raise a path-specific validation error."""
    if not isinstance(value, list):
        raise ModelYearDataError(f"{path} must be a JSON array")
    return value


def _remove_id_fields(value: object) -> None:
    """Remove literal ``id`` fields recursively from a JSON-compatible value."""
    if isinstance(value, dict):
        value.pop("id", None)
        for child in value.values():
            _remove_id_fields(child)
    elif isinstance(value, list):
        for child in value:
            _remove_id_fields(child)


def _reject_id_fields(value: object, path: str) -> None:
    """Reject literal ``id`` fields anywhere in a linked document."""
    if isinstance(value, dict):
        if "id" in value:
            raise ModelYearDataError(f"{path} must not contain id")
        for key, child in value.items():
            _reject_id_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for position, child in enumerate(value):
            _reject_id_fields(child, f"{path}[{position}]")


def _version_code(value: str, fallback: str) -> str:
    """Convert a version name into a stable ASCII identifier."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    code = "_".join(re.findall(r"[A-Za-z0-9]+", ascii_value)).upper()
    return code or fallback


def _version_codes(
    versions: Sequence[dict[str, Any]],
    supplied: Sequence[str] | None,
) -> list[str]:
    """Return unique version codes supplied by the caller or derived from names."""
    if supplied is not None:
        if isinstance(supplied, (str, bytes)) or len(supplied) != len(versions):
            raise ModelYearDataError(
                "version_codes must contain exactly one value for each version"
            )
        raw_codes = list(supplied)
    else:
        raw_codes = []
        for number, version in enumerate(versions, start=1):
            version_data = _object(
                version.get("versionData"),
                f"versions[{number - 1}].versionData",
            )
            existing = version_data.get("versionCode")
            name = version_data.get("marketingName")
            if isinstance(existing, str) and existing.strip():
                raw_codes.append(existing.strip())
            elif isinstance(name, str) and name.strip():
                raw_codes.append(_version_code(name, f"VERSION_{number}"))
            else:
                raw_codes.append(f"VERSION_{number}")

    result: list[str] = []
    counts: dict[str, int] = {}
    for number, value in enumerate(raw_codes, start=1):
        if not isinstance(value, str) or not value.strip():
            raise ModelYearDataError(
                f"version_codes[{number - 1}] must be a non-empty string"
            )
        base = _version_code(value, f"VERSION_{number}")
        occurrence = counts.get(base, 0) + 1
        counts[base] = occurrence
        result.append(base if occurrence == 1 else f"{base}_{occurrence}")
    return result


def validate_model_year_links(model_year: Mapping[str, Any]) -> None:
    """Verify version codes and positional option-to-version relationships."""
    document = _object(model_year, "model_year")
    _reject_id_fields(document, "model_year")
    versions = _array(document.get("versions"), "versions")
    options = _array(document.get("options"), "options")
    if not versions:
        raise ModelYearDataError("versions must contain at least one version")

    version_codes: list[str] = []
    for position, raw_version in enumerate(versions):
        version = _object(raw_version, f"versions[{position}]")
        version_data = _object(
            version.get("versionData"), f"versions[{position}].versionData"
        )
        version_code = version_data.get("versionCode")
        if not isinstance(version_code, str) or not version_code:
            raise ModelYearDataError(
                f"versions[{position}].versionData.versionCode must be a non-empty string"
            )
        version_codes.append(version_code)

    if len(set(version_codes)) != len(version_codes):
        raise ModelYearDataError("version codes must be unique")

    for option_position, raw_option in enumerate(options):
        option = _object(raw_option, f"options[{option_position}]")
        statuses = _array(
            option.get("versionStatus"),
            f"options[{option_position}].versionStatus",
        )
        if len(statuses) != len(versions):
            raise ModelYearDataError(
                f"options[{option_position}].versionStatus must contain "
                f"{len(versions)} entries, found {len(statuses)}"
            )
        for status_position, raw_status in enumerate(statuses):
            _object(
                raw_status,
                f"options[{option_position}].versionStatus[{status_position}]",
            )


def link_model_year_options(
    model_year: Mapping[str, Any],
    *,
    version_codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """
    Link every option status to a version by its array position.

    The returned document is a deep copy with every literal ``id`` field
    removed. Each option must have exactly one ``versionStatus`` entry per
    version in the same order as the ``versions`` array. Existing version codes
    are retained, while missing codes are derived from version marketing names.
    """
    linked = deepcopy(_object(model_year, "model_year"))
    _remove_id_fields(linked)
    versions = _array(linked.get("versions"), "versions")
    options = _array(linked.get("options"), "options")
    if not versions:
        raise ModelYearDataError("versions must contain at least one version")

    version_objects = [
        _object(version, f"versions[{position}]")
        for position, version in enumerate(versions)
    ]
    codes = _version_codes(version_objects, version_codes)
    for position, (version, code) in enumerate(zip(version_objects, codes)):
        version_data = _object(
            version.get("versionData"), f"versions[{position}].versionData"
        )
        version_data["versionCode"] = code

    for option_position, raw_option in enumerate(options):
        option = _object(raw_option, f"options[{option_position}]")
        statuses = _array(
            option.get("versionStatus"),
            f"options[{option_position}].versionStatus",
        )
        if len(statuses) != len(version_objects):
            raise ModelYearDataError(
                f"options[{option_position}].versionStatus must contain "
                f"{len(version_objects)} entries, found {len(statuses)}"
            )
        for status_position, raw_status in enumerate(statuses):
            _object(
                raw_status,
                f"options[{option_position}].versionStatus[{status_position}]",
            )

    validate_model_year_links(linked)
    return linked


def _schema_type_matches(value: object, expected: str) -> bool:
    """Return whether a JSON value has one JSON Schema primitive type."""
    checks = {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ),
        "boolean": isinstance(value, bool),
    }
    return checks.get(expected, True)


def _resolve_local_reference(root: Mapping[str, Any], reference: str) -> object:
    """Resolve one local JSON Pointer used by ``$ref``."""
    if not reference.startswith("#/"):
        raise JsonSchemaValidationError(
            f"Only local JSON Schema references are supported: {reference}"
        )
    node: object = root
    for encoded_part in reference[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or part not in node:
            raise JsonSchemaValidationError(
                f"JSON Schema reference does not exist: {reference}"
            )
        node = node[part]
    return node


def _validate_schema_value(
    value: object,
    schema: object,
    root: Mapping[str, Any],
    path: str,
) -> list[str]:
    """Validate a value against the JSON Schema features used by this project."""
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: rejected by the schema"]
    if not isinstance(schema, Mapping):
        return [f"{path}: invalid schema node"]

    errors: list[str] = []
    reference = schema.get("$ref")
    if isinstance(reference, str):
        errors.extend(
            _validate_schema_value(
                value,
                _resolve_local_reference(root, reference),
                root,
                path,
            )
        )

    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        branch_errors = [
            _validate_schema_value(value, alternative, root, path)
            for alternative in alternatives
        ]
        if not any(not branch for branch in branch_errors):
            errors.append(f"{path}: does not satisfy any allowed schema alternative")
            return errors

    expected = schema.get("type")
    if isinstance(expected, str):
        expected_types = [expected]
    elif isinstance(expected, list):
        expected_types = expected
    else:
        expected_types = []
    if expected_types and not any(
        isinstance(item, str) and _schema_type_matches(value, item)
        for item in expected_types
    ):
        errors.append(
            f"{path}: expected {expected_types}, got {type(value).__name__}"
        )
        return errors

    allowed_values = schema.get("enum")
    if isinstance(allowed_values, list) and value not in allowed_values:
        errors.append(f"{path}: {value!r} is not an allowed enum value")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for key, property_schema in properties.items():
                if key in value:
                    errors.extend(
                        _validate_schema_value(
                            value[key], property_schema, root, f"{path}.{key}"
                        )
                    )
            additional = schema.get("additionalProperties", True)
            for key in value.keys() - properties.keys():
                if additional is False:
                    errors.append(f"{path}: additional property {key!r} is not allowed")
                elif isinstance(additional, Mapping) or isinstance(additional, bool):
                    errors.extend(
                        _validate_schema_value(
                            value[key], additional, root, f"{path}.{key}"
                        )
                    )
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path}: required property {key!r} is missing")

    if isinstance(value, list) and "items" in schema:
        for position, item in enumerate(value):
            errors.extend(
                _validate_schema_value(
                    item, schema["items"], root, f"{path}[{position}]"
                )
            )

    if schema.get("format") == "date-time" and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if "T" not in value or parsed.tzinfo is None:
                raise ValueError
        except ValueError:
            errors.append(f"{path}: {value!r} is not an RFC 3339 date-time")
    return errors


def validate_json_schema(value: object, schema: Mapping[str, Any]) -> None:
    """Validate JSON data against the schema features used by ModelYearDTO."""
    root = _object(schema, "schema")
    errors = _validate_schema_value(value, root, root, "$")
    if errors:
        preview = "\n".join(errors[:20])
        remaining = len(errors) - 20
        suffix = f"\n... and {remaining} more error(s)" if remaining > 0 else ""
        raise JsonSchemaValidationError(preview + suffix)


def link_model_year_options_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    schema_path: str | Path | None = None,
    version_codes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Link a model-year JSON file, optionally validate it, and write it atomically."""
    source = Path(input_path)
    target = Path(output_path)
    try:
        raw_document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelYearDataError(f"Cannot read model-year JSON from {source}") from exc
    linked = link_model_year_options(raw_document, version_codes=version_codes)

    if schema_path is not None:
        schema_source = Path(schema_path)
        try:
            raw_schema = json.loads(schema_source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JsonSchemaValidationError(
                f"Cannot read JSON Schema from {schema_source}"
            ) from exc
        validate_json_schema(linked, _object(raw_schema, "schema"))

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(linked, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return linked


__all__ = [
    "JsonSchemaValidationError",
    "ModelYearDataError",
    "link_model_year_options",
    "link_model_year_options_file",
    "validate_json_schema",
    "validate_model_year_links",
]
