from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List, Tuple


def read_csv_rows(csv_path: Path) -> List[dict[str, str]]:
    encodings = ("utf-8-sig", "utf-8", "gbk", "gb18030")
    last_error = None

    for encoding in encodings:
        try:
            with csv_path.open("r", encoding=encoding, newline="") as file:
                return list(csv.DictReader(file))
        except UnicodeDecodeError as exc:
            last_error = exc

    raise UnicodeDecodeError(
        last_error.encoding if last_error else "unknown",
        last_error.object if last_error else b"",
        last_error.start if last_error else 0,
        last_error.end if last_error else 0,
        "Unable to decode CSV with supported encodings.",
    )


def normalize_name(name: str) -> str:
    normalized = name.strip()
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = normalized.replace("疙瘩", "圪塔")

    # Some edge names use gate structure wording while the unit table uses gate house wording.
    if normalized.endswith("节制闸"):
        normalized = normalized[:-3] + "闸房"

    return normalized


def build_unit_map(unit_rows: List[dict[str, str]]) -> Dict[str, dict[str, str]]:
    unit_map: Dict[str, dict[str, str]] = {}
    for row in unit_rows:
        unit_name = row.get("unitname", "").strip()
        unit_code = row.get("unitcd", "").strip()
        parent_name = row.get("punitcd", "").strip()
        if unit_name and unit_code:
            unit_map[normalize_name(unit_name)] = {
                "unitcd": unit_code,
                "manager": parent_name,
            }
    return unit_map


def update_gates_rows(
    gate_rows: List[dict[str, str]], unit_map: Dict[str, dict[str, str]]
) -> Tuple[List[dict[str, str]], List[str]]:
    updated_rows: List[dict[str, str]] = []
    unmatched: List[str] = []

    for row in gate_rows:
        updated_row = dict(row)
        region = row.get("region", "").strip()
        matched_info = unit_map.get(normalize_name(region))
        if matched_info:
            updated_row["nIndex"] = matched_info["unitcd"]
            if "manager" in updated_row:
                updated_row["manager"] = matched_info["manager"]
        else:
            updated_row["nIndex"] = ""
            if "manager" in updated_row:
                updated_row["manager"] = ""
            unmatched.append(region)
        updated_rows.append(updated_row)

    return updated_rows, unmatched


def write_csv_rows(csv_path: Path, rows: List[dict[str, str]], fieldnames: List[str]) -> None:
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update Gates_params.csv nIndex values using unitcd from the unit info CSV."
    )
    parser.add_argument(
        "--unit-file",
        default="单元信息_第三列替换为中文.csv",
        help="CSV containing unitcd and unitname columns.",
    )
    parser.add_argument(
        "--gates-file",
        default="Gates_params.csv",
        help="Target gates parameter CSV to update in place.",
    )
    parser.add_argument(
        "--output-file",
        default="",
        help="Optional output CSV path. If omitted, overwrite --gates-file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    unit_file = Path(args.unit_file)
    gates_file = Path(args.gates_file)
    output_file = Path(args.output_file) if args.output_file else gates_file

    unit_rows = read_csv_rows(unit_file)
    gate_rows = read_csv_rows(gates_file)
    if not gate_rows:
        raise ValueError("Gates CSV is empty.")

    fieldnames = list(gate_rows[0].keys())
    unit_map = build_unit_map(unit_rows)
    updated_rows, unmatched = update_gates_rows(gate_rows, unit_map)
    write_csv_rows(output_file, updated_rows, fieldnames)

    print(f"Updated {len(updated_rows) - len(unmatched)} rows in {output_file.resolve()}")
    print(f"Unmatched rows: {len(unmatched)}")
    if unmatched:
        print("Unmatched region names:")
        for name in unmatched:
            print(name)


if __name__ == "__main__":
    main()
