from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import List, Tuple


def read_csv_rows(csv_path: Path) -> Tuple[List[dict[str, str]], str]:
    encodings = ("utf-8-sig", "utf-8", "gbk", "gb18030")
    last_error = None

    for encoding in encodings:
        try:
            with csv_path.open("r", encoding=encoding, newline="") as file:
                return list(csv.DictReader(file)), encoding
        except UnicodeDecodeError as exc:
            last_error = exc

    raise UnicodeDecodeError(
        last_error.encoding if last_error else "unknown",
        last_error.object if last_error else b"",
        last_error.start if last_error else 0,
        last_error.end if last_error else 0,
        "Unable to decode CSV with supported encodings.",
    )


def write_csv_rows(
    csv_path: Path, rows: List[dict[str, str]], fieldnames: List[str], encoding: str
) -> None:
    with csv_path.open("w", encoding=encoding, newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_manager_map(gate_rows: List[dict[str, str]]) -> dict[str, str]:
    return {
        row.get("region", "").strip(): row.get("manager", "").strip()
        for row in gate_rows
        if row.get("region", "").strip()
    }


def update_level1(
    edge_rows: List[dict[str, str]], manager_map: dict[str, str]
) -> tuple[List[dict[str, str]], int, int]:
    updated_rows: List[dict[str, str]] = []
    matched_count = 0
    non_empty_manager_count = 0

    for row in edge_rows:
        updated_row = dict(row)
        target = row.get("target", "").strip()
        if target in manager_map:
            matched_count += 1
            manager = manager_map[target]
            updated_row["level1"] = manager
            if manager:
                non_empty_manager_count += 1
        else:
            updated_row["level1"] = ""
        updated_rows.append(updated_row)

    return updated_rows, matched_count, non_empty_manager_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace edges.csv level1 values using manager values from Gates_params_with_manager.csv."
    )
    parser.add_argument(
        "--edges-file",
        default="edges.csv",
        help="Edges CSV to update.",
    )
    parser.add_argument(
        "--gates-file",
        default="Gates_params_with_manager.csv",
        help="Gates CSV containing region and manager columns.",
    )
    parser.add_argument(
        "--output-file",
        default="",
        help="Optional output CSV path. If omitted, overwrite --edges-file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    edges_file = Path(args.edges_file)
    gates_file = Path(args.gates_file)
    output_file = Path(args.output_file) if args.output_file else edges_file

    edge_rows, edge_encoding = read_csv_rows(edges_file)
    gate_rows, _ = read_csv_rows(gates_file)
    if not edge_rows:
        raise ValueError("Edges CSV is empty.")

    manager_map = build_manager_map(gate_rows)
    updated_rows, matched_count, non_empty_manager_count = update_level1(edge_rows, manager_map)
    fieldnames = list(edge_rows[0].keys())
    write_csv_rows(output_file, updated_rows, fieldnames, edge_encoding)

    print(f"Updated {matched_count} rows in {output_file.resolve()}")
    print(f"Rows with non-empty manager written to level1: {non_empty_manager_count}")


if __name__ == "__main__":
    main()
