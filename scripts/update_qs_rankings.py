#!/usr/bin/env python3
"""Regenerate phdhub/qs_top500.py from an official QS ranking workbook."""

from __future__ import annotations

import argparse
import ast
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _column_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref).group(0)
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - ord("A") + 1
    return value - 1


def read_first_sheet(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as workbook:
        strings_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        strings = [
            "".join(node.text or "" for node in item.findall(".//x:t", NS))
            for item in strings_root.findall("x:si", NS)
        ]
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))

    rows = []
    for row in sheet.findall(".//x:sheetData/x:row", NS):
        values: dict[int, str] = {}
        for cell in row.findall("x:c", NS):
            value_node = cell.find("x:v", NS)
            if value_node is None:
                continue
            value = value_node.text or ""
            if cell.get("t") == "s":
                value = strings[int(value)]
            values[_column_index(cell.get("r", "A1"))] = value.strip()
        if values:
            rows.append([values.get(i, "") for i in range(max(values) + 1)])
    return rows


def existing_aliases(path: Path) -> dict[str, str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "QS_TOP500"
            for target in node.targets
        ):
            records = ast.literal_eval(node.value)
            return {name: alias for _rank, name, _country, alias in records if alias}
    return {}


def parse_rank(value: str) -> int | None:
    match = re.search(r"\d+", value.replace("=", ""))
    return int(match.group()) if match else None


def ranking_records(rows: list[list[str]]) -> list[tuple[int, str, str]]:
    header_index = next(
        i for i, row in enumerate(rows)
        if "Rank" in row and "Name" in row and "Country/Territory" in row
    )
    header = rows[header_index]
    rank_col = header.index("Rank")
    name_col = header.index("Name")
    country_col = header.index("Country/Territory")
    country_names = {
        "United States of America": "United States",
        "China (Mainland)": "China",
        "Hong Kong SAR, China": "Hong Kong",
        "Taiwan, China": "Taiwan",
        "Macau SAR, China": "Macau",
        "Macao SAR, China": "Macau",
        "South Korea": "South Korea",
        "Republic of Korea": "South Korea",
        "Russian Federation": "Russia",
        "Iran (Islamic Republic of)": "Iran",
        "Brunei Darussalam": "Brunei",
        "Türkiye": "Turkey",
    }
    records = []
    for row in rows[header_index + 1:]:
        rank = parse_rank(row[rank_col] if rank_col < len(row) else "")
        if rank is None or rank > 500:
            continue
        name = row[name_col].strip() if name_col < len(row) else ""
        country = row[country_col].strip() if country_col < len(row) else ""
        name = re.sub(r"\s+\([^()]+\)$", "", name)
        country = country_names.get(country, country)
        if name and country:
            records.append((rank, name, country))
    return records


def metadata_suffix(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    marker = "\nCONTINENTS = {"
    if marker not in text:
        raise SystemExit(f"Missing CONTINENTS metadata in {path}")
    return text[text.index(marker) + 1:]


def render(
    records: list[tuple[int, str, str]],
    aliases: dict[str, str],
    edition: str,
    suffix: str,
) -> str:
    lines = [
        f'"""Auto-generated from QS World University Rankings {edition} (top 500).',
        "Single source of truth for the university dropdown, QS-rank lookup, and",
        "school-name normalization on import. Do not edit by hand - regenerate.",
        '"""',
        "",
        "# (rank, official_name, country, alias)",
        "QS_TOP500 = [",
    ]
    for rank, name, country in records:
        lines.append(f"    ({rank}, {name!r}, {country!r}, {aliases.get(name, '')!r}),")
    lines.extend(["]", "", f'QS_EDITION = "{edition}"', "", suffix.rstrip(), ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--edition", default="2027")
    parser.add_argument("--output", type=Path, default=Path("phdhub/qs_top500.py"))
    args = parser.parse_args()

    aliases = existing_aliases(args.output)
    suffix = metadata_suffix(args.output)
    records = ranking_records(read_first_sheet(args.workbook))
    if len(records) < 490:
        raise SystemExit(f"Refusing to write only {len(records)} top-500 records")
    args.output.write_text(render(records, aliases, args.edition, suffix), encoding="utf-8")
    print(f"Wrote {len(records)} records to {args.output}")


if __name__ == "__main__":
    main()
