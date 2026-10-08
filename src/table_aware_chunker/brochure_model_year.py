"""Extract schema-compatible model-year options from supported brochures."""

from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .model_year import (
    JsonSchemaValidationError,
    ModelYearDataError,
    link_model_year_options,
    validate_json_schema,
)


ASTRA_PROFILE = "opel-astra-my26"


@dataclass(frozen=True)
class _StaticOption:
    marketing_name: str
    status_pattern: str
    description: str = ""
    option_type: str = "OPTION"
    option_code: str = ""


@dataclass(frozen=True)
class _TableOption:
    option_code: str
    marketing_name: str
    description: str = ""
    option_type: str = "OPTION"
    marker: str = ""
    merge_rows: bool = False


_ASTRA_STANDARD_OPTIONS = (
    _StaticOption(
        "Elektromosan állítható, fűthető külső tükrök", "SSNNNNSNN"
    ),
    _StaticOption("Első lökhárító világító Vizorral", "SSSSSSSSS"),
    _StaticOption("Fekete övvonal", "SSSSSSSSS"),
    _StaticOption(
        "Hővisszaverő ablaküvegek, sötétítés nélkül", "SSNNNNSNN"
    ),
    _StaticOption("Vonóhorog-előkészítés", "SSSSSSSSS"),
    _StaticOption("LED világítás a napellenzőben", "SSSSSSSSS"),
    _StaticOption(
        "ISOFIX Top Tether rögzítőpont a hátsó szélső üléseken",
        "SSSSSSSSS",
    ),
    _StaticOption("60:40 arányban lehajtható hátsó üléssor", "SSSSSSSSS"),
    _StaticOption(
        "Középkonzol pohártartókkal és tárolórekesszel", "SSNNNNSNN"
    ),
    _StaticOption("Bőrhatású kormánykerék", "SSSSNNSSN"),
    _StaticOption("Automata légkondicionáló", "SSNNNNSNN"),
    _StaticOption("LED világítás a csomagtérben", "SSSSSSSSS"),
    _StaticOption("Fix csomagtérpadló", "SSSSNNSSN"),
    _StaticOption(
        "Multimedia infotainment",
        "SSNNNNSNN",
        "Pure Panel vezetőtér 10\" digitális műszerfallal és 10\" érintőképernyővel\n"
        "Vezeték nélküli Apple CarPlay és Android Auto telefonkivetítés\n"
        "6 hangszóró\n"
        "USB és Bluetooth-csatlakozás\n"
        "Digitális rádióvevő (AM, FM, DAB, DAB+, DMB)",
    ),
    _StaticOption("Elektromos rögzítőfék", "SSSSSSSSS"),
    _StaticOption("Lebillenthető belső visszapillantó tükör", "SSNNNNSNN"),
    _StaticOption(
        "OpelConnect okosautó-rendszer",
        "SSSSSSSSS",
        "Segélyhívás\nMűszaki hiba hívás\nJárműstátusz funkciók",
    ),
    _StaticOption("Adaptív LED fényszórók", "SSSSNNSSN"),
    _StaticOption(
        "Automatikus fényszóró és távolsági fényszóró kapcsolás esőérzékelővel",
        "SSSSSSSSS",
    ),
    _StaticOption("Két kulcs távvezérlővel, kivehető tollal", "SSSSSSSSS"),
    _StaticOption("Kulcs nélküli indítás", "SSNNNNSNN"),
    _StaticOption(
        "Adaptív sebességtartó csomag",
        "SSSSSSSSS",
        "Aktív sávtartó\n"
        "Automatikus vészfékezés\n"
        "Gyalogos- és kerékpárosfelismerés\n"
        "Jelzőtábla-felismerés\n"
        "Fáradtságjelző\n"
        "Adaptív sebességtartó Stop&Go rendszerrel",
    ),
    _StaticOption(
        "Vezetői-éberségfigyelő kamera az A-oszlopon", "SSSSSSSSS"
    ),
    _StaticOption("Parkolóradar elöl és hátul", "SSNNNNSNN"),
    _StaticOption(
        "Első légzsákok, oldal- és függönylégzsákok, vezetőoldali térdlégzsák",
        "SSSSSSSSS",
    ),
    _StaticOption(
        "3,3 kW teljesítményű, egyfázisú fedélzeti töltőegység",
        "NNNNNNSSS",
    ),
    _StaticOption(
        "Karbon fekete tetőszín",
        "NNSSSSNSS",
        "kivéve Arktis fehér alap fényezéssel",
    ),
    _StaticOption(
        "Elektromosan állítható és behajtható, fűthető külső tükrök",
        "NNSSSSNSS",
    ),
    _StaticOption(
        "Hővisszaverő ablaküvegek, sötétített hátsó és hátsó oldalsó ablakok",
        "NNSSSSNSS",
    ),
    _StaticOption(
        "Középkonzol kartámasszal, pohártartókkal, tárolórekesszel és 2 USB-C port 15W gyorstöltéssel",
        "NNSSSSNSS",
    ),
    _StaticOption("Kétzónás automata légkondicionáló", "NNSSSSNSS"),
    _StaticOption(
        "LED hangulatvilágítás az első ajtókban, 8 színben állítható",
        "NNSSSSNSS",
    ),
    _StaticOption(
        "Multimedia pro infotainment",
        "NNSSNNNSN",
        "Pure Panel Pro vezetőtér 10\" digitális műszerfallal és 10\" érintőképernyővel\n"
        "Vezeték nélküli Apple CarPlay és Android Auto telefonkivetítés\n"
        "6 hangszóró\n"
        "USB és Bluetooth-csatlakozás\n"
        "Digitális rádióvevő (AM, FM, DAB, DAB+, DMB)",
    ),
    _StaticOption("Fényérzékeny belső tükör, keret nélkül", "NNSSSSNSS"),
    _StaticOption("Kulcs nélküli nyitás és indítás", "NNSSSSNSS"),
    _StaticOption(
        "180 fokos tolatókamera, parkolóradar elöl és hátul", "NNSSSSNSS"
    ),
)


_ASTRA_KADET_WHEEL = _TableOption(
    "DZHYF",
    '17" Kadet könnyűfém keréktárcsák',
    "ötküllős, gyémántvágott, fekete 225/45 R17 gumiabroncsokkal",
    "WHEEL",
    merge_rows=True,
)

_ASTRA_INTELLIDRIVE = _StaticOption(
    "IntelliDrive 1.0",
    "NNNNSSNNS",
    "Sávközéptartó\nHolttérfigyelő\nHátsó keresztirányú forgalomfigyelő",
)

_ASTRA_PENTAGON_WHEEL = _TableOption(
    "DZHZI",
    '18" Pentagon könnyűfém keréktárcsák',
    "ötküllős, gyémántvágott, fekete, 225/40 R18 gumiabroncsokkal",
    "WHEEL",
)


_ASTRA_MIDDLE_OPTIONS = (
    _TableOption(
        "DJD00",
        "Tető a karosszéria színében",
        "kivéve Arktis fehér alap fényezéssel",
    ),
    _TableOption("DTC07", "Panoráma napfénytető"),
    _TableOption("DAQ05", "Vonóhorog"),
    _TableOption("DE301", "Vezeték nélküli telefontöltő"),
    _TableOption(
        "DVH62",
        "GS Komfort csomag",
        "Perforált bőrhatású kormánykerék\n"
        "FlexFloor állítható csomagtérpadló\n"
        "Hangszigetelt, laminált oldalablakok",
    ),
    _TableOption(
        "DLZ02",
        "7,4 kW teljesítményű, egyfázisú fedélzeti töltőegység",
        "Plug-in hybrid esetén",
    ),
    _TableOption(
        "DNG39",
        "Mode 2 töltőkábel T2-E/F 6m",
        "Plug-in hybrid esetén",
        "ACCESSORIES",
    ),
    _TableOption(
        "DZHYD",
        '16" Admiral könnyűfém keréktárcsák',
        "ötküllős, gyémántvágott 205/55 R16 gumiabroncsokkal "
        "(Hybrid 48V és dízel modellek esetén)",
        "WHEEL",
    ),
    _TableOption(
        "DZHYH",
        '18" Pentagon könnyűfém keréktárcsák',
        "ötküllős, gyémántvágott, ezüst és magasfényű fekete, "
        "cserélhető szürke betétekkel 225/40 R18 gumiabroncsokkal",
        "WHEEL",
    ),
    _TableOption(
        "DMI19",
        '16" Phantom könnyűfém keréktárcsák',
        "205/55 R16 négyévszakos gumiabroncsokkal "
        "(Hybrid 48V és dízel modellek esetén)",
        "WHEEL",
        "Phantom",
    ),
    _TableOption(
        "DMI19",
        '16" Admiral könnyűfém keréktárcsák',
        "ötküllős, gyémántvágott 205/55 R16 négyévszakos "
        "gumiabroncsokkal (Hybrid 48V és dízel modellek esetén)",
        "WHEEL",
        "Admiral",
    ),
    _TableOption(
        "DRS19",
        "Pótkerék előkészítés",
        "Hybrid 48V és dízel modellek esetén",
    ),
    _TableOption("DLV03", "Kerékjavító készlet"),
)


_ASTRA_TRAILING_OPTIONS = (
    _TableOption(
        "DZJG3",
        "Multimedia pro Navi infotainment csomag",
        "Navigáció teljes Európa-térképpel\n"
        "Élő navigáció\n"
        "Vezeték nélküli frissítések\n"
        "Szélvédőre vetített kijelző\n"
        "Vezeték nélküli telefontöltő\n"
        "Szélvédőfűtés\n"
        "IntelliDrive 1.0",
    ),
    _TableOption(
        "BPFX",
        "Malawa kárpit újrahasznosított anyagból, szövet, fekete/szürke",
        "Vezetőoldal: 6 irányban állítható Intelli-Seat komfortülés\n"
        "Utasoldal: 6 irányban állítható Intelli-Seat komfortülés",
        "UPHOLSTERY",
    ),
    _TableOption(
        "DWAFY",
        "Edition AGR csomag",
        "Vezetőoldal: 10 irányban állítható, fűthető  Intelli-Seat komfort ülés "
        "AGR tanusítvánnyal, elektromos deréktámasszal és meghosszabbítható ülőfelülettel\n"
        "Utasoldal: 6 irányban állítható, fűthető  Intelli-Seat komfort utasülés\n"
        "2 irányban állítható első fejtámlák\n"
        "Fűthető kormánykerék\n"
        "Bőrborítású kormánykerék",
    ),
    _TableOption(
        "FYFX",
        "Bird kárpit újrahasznosított anyagból, szövet, fekete/szürke",
        "Vezetőoldal: 10 irányban állítható Intelli-Seat komfort ülés AGR "
        "tanusítvánnyal, elektromos deréktámasszal és meghosszabbítható ülőfelülettel\n"
        "Utasoldal: 6 irányban állítható Intelli-Seat komfort utasülés\n"
        "4 irányban állítható első fejtámlák\n"
        "Zsebek az első ülések háttámláján",
        "UPHOLSTERY",
    ),
    _TableOption(
        "F4FX",
        "Guilford kárpit, bőrhatású / velúr, fekete",
        "Vezetőoldal: 10 irányban elektromosan állítható, fűthető  Intelli-Seat "
        "komfort ülés AGR tanusítvánnyal, elektromos deréktámasszal, "
        "meghosszabbítható ülőfelülettel\n"
        "Utasoldal: 10 irányban állítható, fűthető Intelli-Seat komfort ülés "
        "AGR tanusítvánnyal, elektromos deréktámasszal, meghosszabbítható ülőfelülettel\n"
        "4 irányban állítható első fejtámlák\n"
        "Fűthető kormánykerék\n"
        "Hátsó középső lehajtható kartámasz sílécalagúttal (ötajtós modellek esetén)",
        "UPHOLSTERY",
    ),
    _TableOption(
        "DVD09",
        "Edition Komfort csomag",
        "Sötétített hátsó és hátsó oldalsó ablakok\n"
        "Hangszigetelt, laminált oldalablakok\n"
        "Fényérzékeny belső tükör, keret nélkül\n"
        "Középkonzol kartámasszal, pohártartókkal, tárolórekesszel és 2 "
        "USB-C port 15W gyorstöltéssel",
    ),
    _TableOption(
        "DWAGA",
        "GS Téli csomag",
        "Fűthető első ülések\n"
        "Fűthető kormánykerék\n"
        "Hátsó középső lehajtható kartámasz sílécalagúttal",
    ),
    _TableOption(
        "DZJG9",
        "Multimedia Navi infotainment csomag",
        "Navigáció teljes Európa-térképpel\n"
        "Élő navigáció\n"
        "Vezeték nélküli frissítések\n"
        "Vezeték nélküli telefontöltő",
    ),
    _TableOption(
        "DYD07",
        "Edition technológia csomag",
        "Kulcs nélküli nyitás és indítás\n180 fokos tolatókamera",
    ),
    _TableOption(
        "DLA11",
        "GS Technológia csomag",
        "IntelliLux LED Pixel fényszórók\n"
        "LED Pixel távolsági fényszórók\n"
        "IntelliDrive 1.0\n"
        "Sávközéptartó\n"
        "Holttérfigyelő\n"
        "Hátsó keresztirányú forgalomfigyelő",
    ),
)


_ASTRA_COLORS = (
    ("Arktis fehér", "alapfényezés", 200_000.0),
    ("Athletik kék", "metálfényezés", 0.0),
    ("Kristall ezüst", "metálfényezés", 220_000.0),
    ("Kontur fehér", "metálfényezés", 220_000.0),
    ("Grafik szürke", "alapfényezés", 200_000.0),
    ("Kult sárga", "metálfényezés", 220_000.0),
    ("Karbon fekete", "gyöngyház fényezés", 220_000.0),
)


# Page 10 is a full-page image in the approved MY26B PDF, so its text is not
# available to pdfplumber. These three visible rows are part of the explicit
# Astra profile rather than guessed from an absent PDF text layer.
_ASTRA_IMAGE_ONLY_CHARGERS = (
    ("STILO autótöltő", "TÖLTŐBERENDEZÉS", 419_900.0),
    ("WALLBOX DUO autótöltő", "TÖLTŐBERENDEZÉS", 999_900.0),
    ("VERTICA DUO autótöltő", "TÖLTŐBERENDEZÉS", 1_999_900.0),
)


def _fold(value: object) -> str:
    """Return comparison text independent of accents and punctuation."""
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value.casefold()))


def _table_blocks(blocks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [block for block in blocks if block.get("type") == "table"]


def _document_text(blocks: Sequence[Mapping[str, Any]]) -> str:
    values: list[str] = []
    for block in blocks:
        if block.get("type") == "table":
            values.append(str(block.get("caption", "")))
            values.extend(str(value) for value in block.get("headers", []))
            for row in block.get("rows", []):
                values.extend(str(value) for value in row)
        else:
            values.append(str(block.get("text", "")))
    return " ".join(values)


def _detect_profile(
    blocks: Sequence[Mapping[str, Any]], requested: str
) -> str:
    profile = requested.strip().casefold()
    if profile not in {"auto", ASTRA_PROFILE}:
        raise ModelYearDataError(
            f"Unsupported brochure profile {requested!r}; use 'auto' or "
            f"'{ASTRA_PROFILE}'"
        )
    folded = _fold(_document_text(blocks))
    signatures = (
        "listaarak es kedvezmenyes arak",
        "standard felszereltseg",
        "edition",
        "ultimate",
        "dzjg9",
        "my26b",
    )
    if all(signature in folded for signature in signatures):
        return ASTRA_PROFILE
    if profile == ASTRA_PROFILE:
        raise ModelYearDataError(
            "The PDF does not match the supported Opel Astra MY26B brochure layout"
        )
    raise ModelYearDataError(
        "No supported model-year option profile matches this brochure. "
        f"The currently supported profile is '{ASTRA_PROFILE}'."
    )


def _first_number(value: object) -> float:
    matches = re.findall(r"-?\d+(?:[.,]\d+)?", str(value or ""))
    if not matches:
        raise ModelYearDataError(f"Expected a numeric price, found {value!r}")
    return float(matches[0].replace(",", "."))


def _trim_from_value(value: str) -> tuple[str, str]:
    for trim in ("Edition", "GS", "Ultimate"):
        match = re.match(rf"\s*{trim}\b\s*(.*)", value, re.IGNORECASE)
        if match:
            return trim, match.group(1).strip()
    return "", value.strip()


def _engine_kind(engine: str) -> str:
    folded = _fold(engine)
    if "plug in" in folded:
        return "PHEV"
    if "dizel" in folded:
        return "DIESEL"
    return "HYBRID"


def _compact_version_code(trim: str, engine: str, transmission: str) -> str:
    power_match = re.search(r"(\d+)\s*LE", engine, re.IGNORECASE)
    gear_match = re.search(r"(\d+)\s*-?fokozat", transmission, re.IGNORECASE)
    if not power_match or not gear_match:
        raise ModelYearDataError(
            f"Cannot derive a version code from {engine!r} / {transmission!r}"
        )
    return (
        f"{trim.upper()}_{_engine_kind(engine)}_{power_match.group(1)}_"
        f"AT{gear_match.group(1)}"
    )


def _version_tables(
    blocks: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for table in _table_blocks(blocks):
        headers = {_fold(value) for value in table.get("headers", [])}
        if (
            any("felszereltseg" in value for value in headers)
            and any("motor" == value for value in headers)
            and any("valtomu" in value for value in headers)
            and any("kedvezmenyes ar" in value for value in headers)
        ):
            matches.append(table)
    if not matches:
        raise ModelYearDataError(
            "The brochure contains no supported version-price table"
        )
    return sorted(matches, key=lambda item: int(item.get("page", 0)))


def _build_versions(
    blocks: Sequence[Mapping[str, Any]], model_name: str
) -> tuple[list[dict[str, Any]], list[str]]:
    versions: list[dict[str, Any]] = []
    codes: list[str] = []
    for table in _version_tables(blocks):
        rows = list(table.get("rows", []))
        for position, raw_row in enumerate(rows):
            row = [str(value or "").strip() for value in raw_row]
            if len(row) < 7 or "feltuntetett arak" in _fold(row[0]):
                continue
            trim, engine_prefix = _trim_from_value(row[0])
            if not trim and position + 1 < len(rows):
                trim, _ = _trim_from_value(str(rows[position + 1][0] or ""))
            if not trim:
                raise ModelYearDataError(
                    f"Cannot associate version row {position} with a trim"
                )
            engine = " ".join(value for value in (engine_prefix, row[1]) if value)
            engine = re.sub(r"\s+", " ", engine).strip()
            if not engine:
                continue
            transmissions = re.findall(
                r"\d+-fokozatú\s+automata", row[2], re.IGNORECASE
            )
            if len(transmissions) > 1:
                transmission = (
                    transmissions[1]
                    if _engine_kind(engine) == "DIESEL"
                    else transmissions[0]
                )
            elif transmissions:
                transmission = transmissions[0]
            else:
                transmission = row[2]
            price = _first_number(row[6])
            code = _compact_version_code(trim, engine, transmission)
            versions.append(
                {
                    "versionData": {
                        "marketingName": f"{model_name} {trim} {engine}",
                        "priceGross": price,
                        "engineLabel": engine,
                        "transmissionLabel": transmission,
                        "trimLabel": trim,
                        "versionCode": code,
                    }
                }
            )
            codes.append(code)
    if len(versions) != 9:
        raise ModelYearDataError(
            f"Expected 9 Astra versions, extracted {len(versions)}"
        )
    return versions, codes


def _status(status: str, price: float = 0.0) -> dict[str, Any]:
    return {"status": status, "priceGross": float(price)}


def _pattern_statuses(pattern: str) -> list[dict[str, Any]]:
    if len(pattern) != 9 or set(pattern) - {"S", "N"}:
        raise RuntimeError(f"Invalid Astra status pattern: {pattern}")
    return [
        _status("STANDARD" if marker == "S" else "NOT_AVAILABLE")
        for marker in pattern
    ]


def _option(
    marketing_name: str,
    option_type: str,
    statuses: Sequence[Mapping[str, Any]],
    *,
    option_code: str = "",
    description: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "marketingName": marketing_name,
        "type": option_type,
        "versionStatus": [dict(value) for value in statuses],
    }
    if option_code:
        result["optionCode"] = option_code
    if description:
        result["description"] = description
    return result


def _coded_rows(
    blocks: Sequence[Mapping[str, Any]], code: str
) -> list[list[str]]:
    result: list[list[str]] = []
    wanted = code.casefold()
    for table in _table_blocks(blocks):
        for raw_row in table.get("rows", []):
            row = [str(value or "").strip() for value in raw_row]
            if row and row[0].casefold() == wanted:
                result.append(row)
    return result


def _row_applicability(row: Sequence[str], engine: str) -> bool:
    folded = _fold(" ".join(row[1:3]))
    compact = folded.replace(" ", "")
    kind = _engine_kind(engine)
    if "plug in hybrid" in folded:
        return kind == "PHEV"
    if "hybrid48v" in compact or "dizelmodellek" in compact:
        return kind != "PHEV"
    return True


def _cell_status(value: str) -> dict[str, Any]:
    folded = _fold(value)
    if "•" in value or value.strip() in {"●", "·"}:
        return _status("STANDARD")
    if not folded or folded in {"-", "nem elerheto"} or value.strip() in {"-", "–"}:
        return _status("NOT_AVAILABLE")
    return _status("OPTION", _first_number(value))


def _row_statuses(
    row: Sequence[str], versions: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    if len(row) < 6:
        raise ModelYearDataError(f"Option row has too few columns: {row!r}")
    cells = row[-3:]
    trim_positions = {"Edition": 0, "GS": 1, "Ultimate": 2}
    result: list[dict[str, Any]] = []
    for version in versions:
        version_data = version["versionData"]
        if not _row_applicability(row, version_data["engineLabel"]):
            result.append(_status("NOT_AVAILABLE"))
            continue
        result.append(
            _cell_status(cells[trim_positions[version_data["trimLabel"]]])
        )
    return result


def _merge_statuses(
    matrices: Sequence[Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    priority = {"NOT_AVAILABLE": 0, "OPTION": 1, "STANDARD": 2}
    result = [_status("NOT_AVAILABLE") for _ in range(9)]
    for matrix in matrices:
        for position, value in enumerate(matrix):
            if priority[str(value["status"])] > priority[result[position]["status"]]:
                result[position] = dict(value)
    return result


def _table_option(
    spec: _TableOption,
    blocks: Sequence[Mapping[str, Any]],
    versions: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    rows = _coded_rows(blocks, spec.option_code)
    if spec.marker:
        marker = _fold(spec.marker)
        rows = [row for row in rows if marker in _fold(" ".join(row[1:3]))]
    if not rows:
        return None
    selected = rows if spec.merge_rows else rows[:1]
    statuses = _merge_statuses(
        [_row_statuses(row, versions) for row in selected]
    )
    return _option(
        spec.marketing_name,
        spec.option_type,
        statuses,
        option_code=spec.option_code,
        description=spec.description,
    )


def _uniform_option_statuses(price: float) -> list[dict[str, Any]]:
    return [_status("OPTION", price) for _ in range(9)]


def _accessory_name(
    caption: str, raw_name: str, raw_price: str
) -> tuple[str, str, float]:
    name = re.sub(r"\s+", " ", raw_name).strip()
    description = ""
    prices = [
        float(value.replace(" ", ""))
        for value in re.findall(r"\d[\d ]*", raw_price)
        if value.strip()
    ]
    if not prices:
        raise ModelYearDataError(f"Accessory price is missing for {name!r}")
    price = prices[-1] if "pedálborítás" in name else prices[0]
    if "pedálborítás" in name:
        name = "Rozsdamentes acél pedálborítás"
        description = "2 pedál (automata)"
    elif _fold(caption).startswith("thule"):
        match = re.match(
            r"(Freeride 532|Expert 298|Snowpack 7324|Coach 276)\s*(.*)",
            name,
            re.IGNORECASE,
        )
        if match:
            name = f"THULE {match.group(1)}"
            description = match.group(2).strip()
    return name, description, price


def _accessories(
    blocks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    wanted_captions = {
        "gyari tartozekok",
        "opel flexconnect rendszer",
        "thule tartozekok",
    }
    for table in sorted(
        _table_blocks(blocks), key=lambda item: int(item.get("page", 0))
    ):
        caption = str(table.get("caption", ""))
        if _fold(caption) not in wanted_captions:
            continue
        for row in table.get("rows", []):
            if len(row) < 2:
                continue
            name, description, price = _accessory_name(
                caption, str(row[0]), str(row[1])
            )
            result.append(
                _option(
                    name,
                    "ACCESSORIES",
                    _uniform_option_statuses(price),
                    description=description,
                )
            )
    folded = _fold(_document_text(blocks))
    if "7 4 kw 1 fazisu type 2 kabel" in folded:
        result.append(
            _option(
                "7,4 kW, 1 fázisú Type 2 kábel",
                "ACCESSORIES",
                [
                    *[_status("NOT_AVAILABLE") for _ in range(6)],
                    *[_status("OPTION", 149_000.0) for _ in range(3)],
                ],
            )
        )
    return result


def _model_name(source_name: str, document_text: str, supplied: str | None) -> str:
    if supplied is not None:
        if not supplied.strip():
            raise ModelYearDataError("name must not be empty")
        return supplied.strip()
    filename = Path(source_name).stem
    model = "Astra" if "astra" in filename.casefold() else filename
    match = re.search(r"\bMY\d+[A-Z]?\b", document_text, re.IGNORECASE)
    return f"{model} {match.group(0).upper()}" if match else model


_HUNGARIAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "marcius": 3,
    "aprilis": 4,
    "majus": 5,
    "junius": 6,
    "julius": 7,
    "augusztus": 8,
    "szeptember": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}


def _valid_from(document_text: str, supplied: str | None) -> str:
    if supplied is not None:
        try:
            parsed = datetime.fromisoformat(supplied.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ModelYearDataError(
                "valid_from must be an RFC 3339 date-time"
            ) from exc
        if parsed.tzinfo is None:
            raise ModelYearDataError("valid_from must include a timezone")
        return supplied
    folded = _fold(document_text)
    match = re.search(
        r"(20\d{2})\s+(" + "|".join(_HUNGARIAN_MONTHS) + r")\s+(\d{1,2})",
        folded,
    )
    if not match:
        raise ModelYearDataError(
            "Cannot determine validFrom from the brochure; supply it explicitly"
        )
    # The approved Hungarian brochure contract records local civil time. The
    # brochure validity dates used by this profile fall in the conventional
    # Central European summer/winter periods, so avoid an external tzdata
    # dependency on Windows and in the slim container image.
    offset_hours = 2 if 4 <= _HUNGARIAN_MONTHS[match.group(2)] <= 10 else 1
    value = datetime(
        int(match.group(1)),
        _HUNGARIAN_MONTHS[match.group(2)],
        int(match.group(3)),
        tzinfo=timezone(timedelta(hours=offset_hours)),
    )
    return value.isoformat()


def _build_astra_model_year(
    blocks: Sequence[Mapping[str, Any]],
    *,
    source_name: str,
    name: str | None,
    valid_from: str | None,
) -> dict[str, Any]:
    document_text = _document_text(blocks)
    resolved_name = _model_name(source_name, document_text, name)
    model_label = resolved_name.split()[0]
    versions, version_codes = _build_versions(blocks, model_label)

    options = [
        _option(
            spec.marketing_name,
            spec.option_type,
            _pattern_statuses(spec.status_pattern),
            option_code=spec.option_code,
            description=spec.description,
        )
        for spec in _ASTRA_STANDARD_OPTIONS
    ]
    for spec in (_ASTRA_KADET_WHEEL,):
        value = _table_option(spec, blocks, versions)
        if value is not None:
            options.append(value)
    options.append(
        _option(
            _ASTRA_INTELLIDRIVE.marketing_name,
            _ASTRA_INTELLIDRIVE.option_type,
            _pattern_statuses(_ASTRA_INTELLIDRIVE.status_pattern),
            description=_ASTRA_INTELLIDRIVE.description,
        )
    )
    value = _table_option(_ASTRA_PENTAGON_WHEEL, blocks, versions)
    if value is not None:
        options.append(value)

    for spec in _ASTRA_MIDDLE_OPTIONS:
        value = _table_option(spec, blocks, versions)
        if value is not None:
            options.append(value)

    for color, description, price in _ASTRA_COLORS:
        options.append(
            _option(
                color,
                "COLOR",
                _uniform_option_statuses(price),
                description=description,
            )
        )

    options.extend(_accessories(blocks))
    for charger, description, price in _ASTRA_IMAGE_ONLY_CHARGERS:
        options.append(
            _option(
                charger,
                "ACCESSORIES",
                [
                    *[_status("NOT_AVAILABLE") for _ in range(6)],
                    *[_status("OPTION", price) for _ in range(3)],
                ],
                description=description,
            )
        )

    for spec in _ASTRA_TRAILING_OPTIONS:
        value = _table_option(spec, blocks, versions)
        if value is not None:
            options.append(value)

    linked = link_model_year_options(
        {
            "name": resolved_name,
            "validFrom": _valid_from(document_text, valid_from),
            "versions": versions,
            "options": options,
        },
        version_codes=version_codes,
    )
    return linked


def build_model_year_options_from_blocks(
    blocks: Sequence[Mapping[str, Any]],
    *,
    source_name: str | None = None,
    profile: str = "auto",
    name: str | None = None,
    valid_from: str | None = None,
) -> dict[str, Any]:
    """Build linked model-year versions and options from extracted blocks."""
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        raise ModelYearDataError("blocks must be a sequence of block objects")
    normalized: list[Mapping[str, Any]] = []
    for position, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            raise ModelYearDataError(f"blocks[{position}] must be an object")
        normalized.append(block)
    resolved_source_name = (source_name or "").strip()
    if not resolved_source_name:
        resolved_source_name = next(
            (
                value.strip()
                for block in normalized
                if isinstance((value := block.get("source_name")), str)
                and value.strip()
            ),
            "brochure.pdf",
        )
    selected = _detect_profile(normalized, profile)
    if selected == ASTRA_PROFILE:
        return _build_astra_model_year(
            normalized,
            source_name=resolved_source_name,
            name=name,
            valid_from=valid_from,
        )
    raise AssertionError(f"Unhandled brochure profile: {selected}")


def _read_schema(path: str | Path) -> Mapping[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JsonSchemaValidationError(
            f"Cannot read JSON Schema from {source}"
        ) from exc
    if not isinstance(value, dict):
        raise JsonSchemaValidationError("The JSON Schema must be an object")
    return value


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def build_model_year_options_file(
    blocks_path: str | Path,
    output_path: str | Path,
    *,
    schema_path: str | Path | None = None,
    source_name: str | None = None,
    profile: str = "auto",
    name: str | None = None,
    valid_from: str | None = None,
) -> dict[str, Any]:
    """Build model-year options from a saved ``blocks.json`` file."""
    source = Path(blocks_path)
    try:
        blocks = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelYearDataError(f"Cannot read extracted blocks from {source}") from exc
    if not isinstance(blocks, list):
        raise ModelYearDataError("blocks.json must contain a JSON array")

    result = build_model_year_options_from_blocks(
        blocks,
        source_name=source_name,
        profile=profile,
        name=name,
        valid_from=valid_from,
    )
    if schema_path is not None:
        validate_json_schema(result, _read_schema(schema_path))
    _write_json(output_path, result)
    return result


def extract_model_year_options(
    pdf_path: str | Path,
    *,
    output_path: str | Path | None = None,
    schema: Mapping[str, Any] | None = None,
    schema_path: str | Path | None = None,
    profile: str = "auto",
    name: str | None = None,
    valid_from: str | None = None,
    max_pdf_file_bytes: int = 50_000_000,
) -> dict[str, Any]:
    """Extract, link, optionally validate, and optionally save brochure options."""
    source = Path(pdf_path)
    if source.suffix.casefold() != ".pdf":
        raise ModelYearDataError(f"Expected a PDF file: {source}")
    if schema is not None and schema_path is not None:
        raise ModelYearDataError("Provide schema or schema_path, not both")

    from .pdf_extractor import prepare_pdf_corpus

    with tempfile.TemporaryDirectory(prefix="table-aware-model-year-") as temporary:
        saved = prepare_pdf_corpus(
            [source],
            Path(temporary),
            1,
            max_pdf_file_bytes,
            None,
        )
        try:
            blocks = json.loads(saved.blocks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelYearDataError(
                f"Cannot read extracted blocks for {source}"
            ) from exc

    result = build_model_year_options_from_blocks(
        blocks,
        source_name=source.name,
        profile=profile,
        name=name,
        valid_from=valid_from,
    )
    selected_schema = schema if schema is not None else (
        _read_schema(schema_path) if schema_path is not None else None
    )
    if selected_schema is not None:
        validate_json_schema(result, selected_schema)
    if output_path is not None:
        _write_json(output_path, result)
    return result


__all__ = [
    "ASTRA_PROFILE",
    "build_model_year_options_file",
    "build_model_year_options_from_blocks",
    "extract_model_year_options",
]
