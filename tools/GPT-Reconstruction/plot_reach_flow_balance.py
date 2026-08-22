from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Mapping

import matplotlib

matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt
import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = SCRIPT_DIR.parent
PROJECT_DIR = TOOLS_DIR.parent
DEFAULT_CSV = (
    PROJECT_DIR
    / "data"
    / "sj_zonggan-d0"
    / "output"
    / "pid_single_reach_reconstruction"
    / "6"
    / "assimilated_series.csv"
)


def _configure_font() -> None:
    for font_path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            font_manager.fontManager.addfont(font_path)
        except Exception:
            pass
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def _series(rows: list[dict[str, str]], name: str) -> list[float]:
    values = []
    for row in rows:
        text = (row.get(name) or "").strip()
        values.append(float(text) if text else np.nan)
    return values


def _has(rows: list[dict[str, str]], name: str) -> bool:
    return any((row.get(name) or "").strip() for row in rows)


def _first_text(rows: list[dict[str, str]], name: str, default: str = "") -> str:
    for row in rows:
        text = (row.get(name) or "").strip()
        if text:
            return text
    return default


def _gate_label(
    rows: list[dict[str, str]],
    name_key: str,
    id_key: str,
    fallback: str,
) -> str:
    name = _first_text(rows, name_key, fallback)
    gate_id = _first_text(rows, id_key)
    return _format_gate_label(name, gate_id)


def _format_gate_label(name: str, gate_id: str | int | None) -> str:
    return f"{name}（id={gate_id}）" if gate_id not in (None, "") else name


def main(
    argv: list[str] | None = None,
    *,
    gate_info: Mapping[str, str] | None = None,
    boundary_gate_id: str | int | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="单独绘制渠段入流/分水/出流")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="assimilated_series.csv 路径")
    parser.add_argument("--out", type=Path, default=None, help="输出 PNG 路径")
    parser.add_argument("--set", action="store_true", help="额外显示设定分水流量")
    parser.add_argument("--obs", action="store_true", help="额外显示实测散点")
    args = parser.parse_args(argv)

    csv_path = Path(args.csv).resolve()
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig", newline="")))
    if not rows:
        print("CSV 为空")
        return 2

    _configure_font()
    x = list(range(len(rows)))
    segment = rows[0].get("segment") or csv_path.parent.name

    fig, ax = plt.subplots(figsize=(14, 7), dpi=150)
    fig.patch.set_facecolor("#f8fafc")

    if _has(rows, "upstream_gate_q_analysis"):
        ax.plot(
            x,
            _series(rows, "upstream_gate_q_analysis"),
            color="#0f766e",
            linewidth=2.0,
            linestyle="--",
            label=f"渠段入流 {_gate_label(rows, 'upstream_gate_name', 'upstream_gate_id', '上游闸')}",
        )

    if _has(rows, "q_boundary_dll_analysis"):
        boundary_id = _first_text(
            rows,
            "boundary_gate_id",
            str(boundary_gate_id) if boundary_gate_id is not None else "",
        )
        boundary_name = _first_text(rows, "boundary_gate_name")
        if not boundary_name and gate_info is not None and boundary_id:
            boundary_name = str(gate_info.get(boundary_id, ""))
        if not boundary_name:
            boundary_name = _first_text(rows, "boundary_model_name", "分水边界")
        boundary_gate_label = _format_gate_label(boundary_name, boundary_id)
        ax.plot(
            x,
            _series(rows, "q_boundary_dll_analysis"),
            color="#f59e0b",
            linewidth=1.8,
            linestyle=":",
            marker="o",
            markersize=4,
            markevery=max(1, len(rows) // 20),
            label=f"分水流量 {boundary_gate_label}",
        )
    if args.set and _has(rows, "q_boundary_analysis"):
        ax.plot(
            x,
            _series(rows, "q_boundary_analysis"),
            color="#16a34a",
            linewidth=1.6,
            linestyle="-",
            label="分水流量（设定值）",
        )

    if _has(rows, "gate_q_analysis"):
        ax.plot(
            x,
            _series(rows, "gate_q_analysis"),
            color="#b91c1c",
            linewidth=2.0,
            linestyle="-.",
            label=f"渠段出流 {_gate_label(rows, 'target_gate_name', 'target_gate_id', '下游闸')}",
        )

    if args.obs:
        if _has(rows, "q_boundary_obs"):
            ax.scatter(
                x,
                _series(rows, "q_boundary_obs"),
                color="#dc2626",
                s=22,
                label="实测：分水流量",
                zorder=4,
            )
        if _has(rows, "gate_q_obs"):
            ax.scatter(
                x,
                _series(rows, "gate_q_obs"),
                color="#b91c1c",
                marker="s",
                s=22,
                label="实测：下游闸流量",
                zorder=4,
            )

    inflow_values = np.asarray(_series(rows, "upstream_gate_q_analysis"), dtype=float)
    outflow_values = np.asarray(_series(rows, "gate_q_analysis"), dtype=float)
    combined = np.concatenate([inflow_values, outflow_values])
    finite = combined[np.isfinite(combined)]
    ymax = float(np.max(finite)) if finite.size else 1.0
    ax.set_ylim(bottom=-0.05 * ymax)

    ax.set_xlabel("时间步")
    ax.set_ylabel("流量")
    ax.set_title(f"{segment} 渠段入流 / 分水 / 出流", loc="left")
    ax.grid(True, color="#e2e8f0", linewidth=0.8)
    ax.legend(loc="best")

    if rows:
        tick_idx = sorted({0, len(rows) // 4, len(rows) // 2, 3 * len(rows) // 4, len(rows) - 1})
        ax.set_xticks(tick_idx)
        ax.set_xticklabels(
            [rows[idx].get("time", str(idx)) for idx in tick_idx],
            rotation=12,
            ha="right",
        )

    fig.tight_layout()
    out_path = Path(args.out) if args.out else csv_path.parent / "reach_flow_balance.png"
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
