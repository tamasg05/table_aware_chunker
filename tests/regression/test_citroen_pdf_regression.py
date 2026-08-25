"""Document-level regression tests for the Citroën multi-column PDF fixture."""

from __future__ import annotations

import unittest
from pathlib import Path

from table_aware_chunker import render_block, validate_blocks
from table_aware_chunker.pdf_extractor import extract_pdf_blocks


ARTIFACTS = Path(__file__).parents[1] / "artifacts"
FILENAME = "C3_Aircross_2026_1.pdf"


class CitroenPdfRegressionTests(unittest.TestCase):
    """Check the three-column equipment lists on page 3."""

    @classmethod
    def setUpClass(cls):
        path = ARTIFACTS / FILENAME
        if not path.is_file():
            raise AssertionError(f"Missing PDF regression artifact: {path}")
        cls.blocks, cls.source = extract_pdf_blocks(path)
        validate_blocks(cls.blocks)

    def test_source_metadata(self):
        self.assertEqual(self.source["kind"], "pdf")
        self.assertEqual(self.source["filename"], FILENAME)
        self.assertEqual(self.source["pages"], 9)

    def _table_on_page(self, page: int) -> dict:
        return next(
            block
            for block in self.blocks
            if block.get("page") == page and block.get("type") == "table"
        )

    def test_page_two_price_table_omits_visual_spacer_columns(self):
        table = self._table_on_page(2)

        self.assertEqual(table["headers"], ["Motor-váltó", "YOU", "PLUS", "MAX"])
        self.assertEqual(len(table["rows"]), 4)
        self.assertEqual(
            table["rows"][0],
            [
                "Turbo 100 LE manuális Listaár",
                "7190000 Ft",
                "7990000 Ft",
                "8690000 Ft",
            ],
        )
        self.assertTrue(all(all(row) for row in table["rows"]))

    def test_page_three_equipment_columns_retain_trim_labels(self):
        table = self._table_on_page(3)
        you = (
            "Citroën Advanced Comfort® futómű Citroën vetített műszeregység "
            "LED nappali menetfény, Halogén fényszórók "
            "Automata távolságifényszóró-kapcsolás 16\" egyszínű acél "
            "keréktárcsák Manuális (Turbo 100) / elektromos rögzítőfék "
            "(Hibrid 145 és EV 113) Hátsó parkolószenzorok Manuális "
            "légkondicionáló Elektromos első ablakemelők Elektromosan "
            "állítható visszapillantótükrök Magasságában állítható "
            "vezetőoldali ülés 1/3–2/3 arányban lehajtható hátsó ülések "
            "Isofix rögzítési pontok és Top Tether a hátsó szélső üléseken "
            "My Citroën Play okostelefontartóval Urban Bronze beltérhangulat"
        )
        plus = (
            "17\" Steel & Style acél keréktárcsák Eco LED fényszórók "
            "Automata légkondicionáló Tolatókamera hátsó "
            "parkolószenzorokkal 10,25\"\" érintőképernyő vezeték nélküli "
            "telefontükrözéssel4, rádióval Elektromos első és hátsó "
            "ablakemelők Elektromosan behajtható, fűthető visszapillantók "
            "Intelligens távolságifényszóró-vezérlés 2 db USB-C csatlakozó "
            "a 2. üléssorban Hangszigetelt szélvédő Bőrhatású kormány Két "
            "magasságban rögzíthető csomagtérpadló Color Clip színbetétek "
            "Citroën Advanced Comfort® ülések"
        )
        maximum = (
            "Karosszériától eltérő színű tető Kulcsnélküli "
            "nyitás-zárás-indítás (Hibrid 145 és EV 113) Holttérfigyelő "
            "17\" gyémántvágott könnyűfém felni LED hátsó lámpák "
            "Tolatókamera első és hátsó parkolószenzorokkal Fényre "
            "sötétedő belső visszapillantótükör My Citroën Drive: Navigáció "
            "10,25\"\" érintőképernyővel Vezeték nélküli telefontöltő "
            "Metropolitan Grey beltérhangulat Szövet- és "
            "bőrhatásúszövet-kárpit"
        )

        self.assertEqual(
            table["headers"],
            [
                "YOU",
                "PLUS (YOU felszereltségen felül)",
                "MAX (PLUS felszereltségen felül)",
            ],
        )
        self.assertEqual(table["rows"], [[you, plus, maximum]])
        rendered = render_block(table)
        self.assertIn(f"YOU = {you}", rendered)
        self.assertIn(f"PLUS (YOU felszereltségen felül) = {plus}", rendered)
        self.assertIn(f"MAX (PLUS felszereltségen felül) = {maximum}", rendered)

        page_blocks = [block for block in self.blocks if block.get("page") == 3]
        table_position = page_blocks.index(table)
        self.assertEqual(
            page_blocks[table_position - 1].get("text"),
            "Az alapfelszereltség főbb elemei:",
        )
        self.assertEqual(
            page_blocks[table_position + 1].get("text"),
            "ELEKTROMOS VERZIÓK EXTRA FELSZERELTSÉGE",
        )


if __name__ == "__main__":
    unittest.main()
