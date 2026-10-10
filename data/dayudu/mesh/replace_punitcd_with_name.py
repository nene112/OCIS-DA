from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import List


def read_rows(csv_path: Path) -> List[List[str]]:
    """Read CSV rows, trying common encodings used by local data files."""
    encodings = ("utf-8-sig", "utf-8", "gbk", "gb18030")
    last_error = None

    for encoding in encodings:
        try:
            with csv_path.open("r", encoding=encoding, newline="") as file:
                return list(csv.reader(file))
        except UnicodeDecodeError as exc:
            last_error = exc

    raise UnicodeDecodeError(
        last_error.encoding if last_error else "unknown",
        last_error.object if last_error else b"",
        last_error.start if last_error else 0,
        last_error.end if last_error else 0,
        "Unable to decode CSV with supported encodings.",
    )



def build_mapping(rows: List[List[str]]) -> dict[str, str]:
    if not rows:
        raise ValueError("CSV file is empty.")

    mapping: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        unitcd = row[0].strip()
        unitname = row[1].strip()
        if unitcd:
            mapping[unitcd] = unitname

    return mapping



def replace_parent_code(rows: List[List[str]], mapping: dict[str, str]) -> List[List[str]]:
    if not rows:
        return rows

    output_rows: List[List[str]] = [rows[0][:]]
    for row in rows[1:]:
        new_row = row[:]
        if len(new_row) >= 3:
            parent_code = new_row[2].strip()
            if parent_code:
                new_row[2] = mapping.get(parent_code, parent_code)
        output_rows.append(new_row)

    return output_rows



def write_rows(csv_path: Path, rows: List[List[str]]) -> None:
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerows(rows)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replace the third column in a CSV using the first-two-column mapping."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="单元信息.csv",
        help="Input CSV path. Defaults to 单元信息.csv in the current directory.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="单元信息_第三列替换为中文.csv",
        help="Output CSV path. Defaults to 单元信息_第三列替换为中文.csv.",
    )
    return parser.parse_args()



def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    rows = read_rows(input_path)
    mapping = build_mapping(rows)
    output_rows = replace_parent_code(rows, mapping)
    write_rows(output_path, output_rows)

    print(f"Processed {len(output_rows) - 1} rows.")
    print(f"Output written to: {output_path.resolve()}")



if __name__ == "__main__":
    main()
