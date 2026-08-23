from __future__ import annotations

import math
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib import font_manager
import matplotlib.pyplot as plt


def _split_isolated_outliers(values):
    numeric = np.asarray(values, dtype=float)
    valid_indices = np.flatnonzero(np.isfinite(numeric))
    normal = numeric.copy()
    outliers = np.full(numeric.shape, np.nan, dtype=float)
    if valid_indices.size < 5:
        return normal.tolist(), outliers.tolist()

    residuals = []
    candidates = []
    for position in range(1, valid_indices.size - 1):
        previous_idx, current_idx, next_idx = valid_indices[position - 1 : position + 2]
        previous_value = numeric[previous_idx]
        current_value = numeric[current_idx]
        next_value = numeric[next_idx]
        interpolated = previous_value + (next_value - previous_value) * (
            (current_idx - previous_idx) / (next_idx - previous_idx)
        )
        residual = abs(current_value - interpolated)
        residuals.append(residual)
        candidates.append((current_idx, residual, abs(next_value - previous_value)))

    residual_array = np.asarray(residuals, dtype=float)
    median = float(np.median(residual_array))
    mad = float(np.median(np.abs(residual_array - median)))
    threshold = median + 6.0 * max(1.4826 * mad, np.finfo(float).eps)
    for current_idx, residual, neighbor_span in candidates:
        if residual > threshold and residual > 3.0 * neighbor_span:
            outliers[current_idx] = numeric[current_idx]
            normal[current_idx] = np.nan
    return normal.tolist(), outliers.tolist()


def write_reconstruction_png(
    out_path: Path,
    rows,
    *,
    segment_name: str,
    mark_flow_outliers: bool = True,
    show_baseline: bool = True,
) -> Path:
    """绘制水位、分水流量以及渠段进出流量与净流量。"""

    for font_path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            font_manager.fontManager.addfont(font_path)
        except Exception:
            pass
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    x = list(range(len(rows)))

    def series(name: str):
        return [float(row[name]) if row.get(name) not in (None, "") else np.nan for row in rows]

    def summed_series(first_name: str, second_name: str):
        first = series(first_name)
        second = series(second_name)
        return [
            first_value + second_value
            if np.isfinite(first_value) and np.isfinite(second_value)
            else np.nan
            for first_value, second_value in zip(first, second)
        ]

    fig, axes = plt.subplots(3, 1, figsize=(16, 14), dpi=180, sharex=True)
    fig.patch.set_facecolor("#f8fafc")

    stage_ax = axes[0]
    if show_baseline:
        stage_ax.plot(
            x,
            series("h1_forecast"),
            color="#2563eb",
            linewidth=1.8,
            linestyle="--",
            label="模拟值：水位",
        )
    stage_ax.plot(
        x,
        series("h1_analysis"),
        color="#16a34a",
        linewidth=1.9,
        linestyle="--",
        label="分析值：水位",
    )
    if any(row.get("h1_control_target") not in (None, "") for row in rows):
        stage_ax.plot(
            x,
            series("h1_control_target"),
            color="#f59e0b",
            linewidth=1.6,
            linestyle="-",
            label="设定值：目标水位",
        )
    stage_ax.scatter(x, series("h1_obs"), color="#dc2626", s=20, label="实测值：水位", zorder=4)
    stage_ax.set_ylabel("水位 (m)")
    stage_ax.set_title(f"{segment_name} 水位同化过程", loc="left")
    stage_ax.grid(True, color="#e2e8f0", linewidth=0.8)
    stage_ax.legend(
        loc="best",
        title="实线=设定值 · 虚线=模拟值/分析值 · 散点=实测值",
        title_fontsize=9,
    )

    flow_ax = axes[1]
    boundary_values = series("q_boundary_obs")
    boundary_obs, boundary_outliers = (
        _split_isolated_outliers(boundary_values)
        if mark_flow_outliers
        else (boundary_values, [np.nan] * len(rows))
    )
    flow_ax.plot(
        x,
        series("q_boundary_forecast"),
        color="#64748b",
        linewidth=1.6,
        label="设定：分水流量（原始）",
    )
    flow_ax.plot(
        x,
        series("q_boundary_analysis"),
        color="#16a34a",
        linewidth=1.9,
        label="设定：分水流量（PID）",
    )
    if show_baseline and any(row.get("q_boundary_dll_forecast") not in (None, "") for row in rows):
        flow_ax.plot(
            x,
            series("q_boundary_dll_forecast"),
            color="#2563eb",
            linewidth=1.5,
            linestyle="--",
            label="模拟：分水流量（DLL·基准）",
        )
    if any(row.get("q_boundary_dll_analysis") not in (None, "") for row in rows):
        flow_ax.plot(
            x,
            series("q_boundary_dll_analysis"),
            color="#f59e0b",
            linewidth=1.7,
            linestyle="--",
            label="模拟：分水流量（DLL·PID）",
        )
    flow_ax.scatter(x, boundary_obs, color="#dc2626", s=20, label="实测：分水流量", zorder=4)
    if mark_flow_outliers:
        flow_ax.scatter(
            x,
            boundary_outliers,
            color="#dc2626",
            marker="x",
            s=64,
            linewidths=1.8,
            label="实测：分水流量异常值（忽略）",
            zorder=6,
        )
    flow_ax.set_ylabel("流量")
    flow_ax.set_title("分水流量调整过程", loc="left")
    flow_ax.grid(True, color="#e2e8f0", linewidth=0.8)
    flow_ax.legend(
        loc="best",
        title="实线=设定值 · 虚线=模拟值/分析值 · 散点=实测值",
        title_fontsize=9,
    )

    reach_flow_ax = axes[2]
    downstream_values = series("gate_q_obs")
    upstream_values = summed_series("q_boundary_obs", "gate_q_obs")
    if mark_flow_outliers:
        downstream_obs, downstream_outliers = _split_isolated_outliers(downstream_values)
        upstream_obs, upstream_outliers = _split_isolated_outliers(upstream_values)
    else:
        downstream_obs, downstream_outliers = downstream_values, [np.nan] * len(rows)
        upstream_obs, upstream_outliers = upstream_values, [np.nan] * len(rows)
    reach_flow_ax.plot(
        x,
        series("q_boundary_analysis"),
        color="#1f2937",
        linewidth=1.6,
        linestyle="-",
        label="设定：边界给定流量",
    )
    if show_baseline and any(row.get("q_boundary_dll_forecast") not in (None, "") for row in rows):
        reach_flow_ax.plot(
            x,
            series("q_boundary_dll_forecast"),
            color="#2563eb",
            linewidth=1.5,
            linestyle=":",
            label="DLL反馈：分水流量（基准）",
        )
    if any(row.get("q_boundary_dll_analysis") not in (None, "") for row in rows):
        reach_flow_ax.plot(
            x,
            series("q_boundary_dll_analysis"),
            color="#f59e0b",
            linewidth=1.7,
            linestyle=":",
            label="DLL反馈：分水流量（PID）",
        )
    simulation_suffix = "forecast" if show_baseline else "analysis"
    simulation_label = "基准" if show_baseline else "PID"
    reach_flow_ax.plot(
        x,
        series(f"upstream_gate_q_{simulation_suffix}"),
        color="#0f766e",
        linewidth=2.0,
        linestyle="--",
        label=f"模拟：上游闸流量（{simulation_label}）",
    )
    reach_flow_ax.scatter(
        x,
        upstream_obs,
        color="#0f766e",
        marker="o",
        s=34,
        edgecolors="white",
        linewidths=0.7,
        label="实测：上游闸流量",
        zorder=5,
    )
    if mark_flow_outliers:
        reach_flow_ax.scatter(
            x,
            upstream_outliers,
            color="#0f766e",
            marker="x",
            s=64,
            linewidths=1.8,
            label="实测：上游闸异常值（忽略）",
            zorder=6,
        )
    reach_flow_ax.plot(
        x,
        series(f"gate_q_{simulation_suffix}"),
        color="#b91c1c",
        linewidth=2.0,
        linestyle="--",
        label=f"模拟：下游闸流量（{simulation_label}）",
    )
    reach_flow_ax.scatter(
        x,
        downstream_obs,
        color="#b91c1c",
        marker="s",
        s=34,
        edgecolors="white",
        linewidths=0.7,
        label="实测：下游闸流量",
        zorder=5,
    )
    if mark_flow_outliers:
        reach_flow_ax.scatter(
            x,
            downstream_outliers,
            color="#b91c1c",
            marker="x",
            s=64,
            linewidths=1.8,
            label="实测：下游闸异常值（忽略）",
            zorder=6,
        )
    reach_flow_ax.plot(
        x,
        series(f"net_gate_q_{simulation_suffix}"),
        color="#7c3aed",
        linewidth=2.2,
        linestyle="--",
        label=f"模拟：渠段净流量（{simulation_label}，入正出负）",
    )
    reach_flow_ax.set_xlabel("时间步")
    reach_flow_ax.set_ylabel("流量")
    reach_flow_ax.set_title(f"{segment_name} 关键流量过程", loc="left")
    reach_flow_ax.grid(True, color="#e2e8f0", linewidth=0.8)
    reach_flow_ax.legend(
        loc="best",
        ncol=3,
        title="实线=设定值 · 虚线=模拟值/分析值 · 散点=实测值",
        title_fontsize=9,
    )

    if rows:
        tick_idx = sorted({0, len(rows) // 4, len(rows) // 2, 3 * len(rows) // 4, len(rows) - 1})
        reach_flow_ax.set_xticks(tick_idx)
        reach_flow_ax.set_xticklabels([rows[idx].get("time", str(idx)) for idx in tick_idx], rotation=12, ha="right")

    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


def _display_name(*names: str, fallback: str = "unknown") -> str:
    for name in names:
        text = str(name or "").strip()
        if text:
            return text
    return fallback


def _has_series_data(values) -> bool:
    for value in values:
        if value is not None:
            return True
    return False


def _signed_flow_sum_series(positive_series_list, negative_series_list):
    lengths = [len(series) for series in list(positive_series_list) + list(negative_series_list) if series is not None]
    if not lengths:
        return []
    size = max(lengths)
    out = []
    for idx in range(size):
        total = 0.0
        has_value = False
        for series in positive_series_list:
            if series is None or idx >= len(series):
                continue
            value = series[idx]
            if value is None:
                continue
            total += float(value)
            has_value = True
        for series in negative_series_list:
            if series is None or idx >= len(series):
                continue
            value = series[idx]
            if value is None:
                continue
            total -= float(value)
            has_value = True
        out.append(total if has_value else None)
    return out


def _classify_gate_flow_regime(h1_series, h2_series, threshold: float = 0.7):
    regimes = []
    for h1_value, h2_value in zip(h1_series, h2_series):
        if h1_value is None or h2_value is None:
            regimes.append(None)
            continue
        h1_scalar = float(h1_value)
        h2_scalar = float(h2_value)
        if not math.isfinite(h1_scalar) or not math.isfinite(h2_scalar) or h1_scalar <= 0.0:
            regimes.append(None)
            continue
        ratio = h2_scalar / h1_scalar
        regimes.append("free" if ratio <= float(threshold) else "submerged")
    return regimes


def _render_regime_background(ax, regimes, free_color="#2563eb", submerged_color="#dc2626", alpha: float = 0.10):
    if not regimes:
        return

    spans = []
    start_idx = None
    current_regime = None
    for idx, regime in enumerate(regimes):
        if regime is None:
            if current_regime is not None:
                spans.append((start_idx, idx - 1, current_regime))
                start_idx = None
                current_regime = None
            continue
        if current_regime is None:
            start_idx = idx
            current_regime = regime
            continue
        if regime != current_regime:
            spans.append((start_idx, idx - 1, current_regime))
            start_idx = idx
            current_regime = regime

    if current_regime is not None:
        spans.append((start_idx, len(regimes) - 1, current_regime))

    for start_idx, end_idx, regime in spans:
        color = free_color if regime == "free" else submerged_color
        ax.axvspan(start_idx - 0.5, end_idx + 0.5, color=color, alpha=alpha, zorder=0)


def _classify_gate_outflow_pattern(h1_series, gate_opening_series, threshold: float = 0.65):
    patterns = []
    for h1_value, gate_opening_value in zip(h1_series, gate_opening_series):
        if h1_value is None or gate_opening_value is None:
            patterns.append(None)
            continue
        h1_scalar = float(h1_value)
        gate_opening_scalar = float(gate_opening_value)
        if not math.isfinite(h1_scalar) or not math.isfinite(gate_opening_scalar) or h1_scalar <= 0.0:
            patterns.append(None)
            continue
        ratio = gate_opening_scalar / h1_scalar
        patterns.append("orifice" if ratio <= float(threshold) else "weir")
    return patterns


def _render_outflow_pattern_background(ax, patterns, alpha: float = 0.18):
    if not patterns:
        return

    spans = []
    start_idx = None
    current_pattern = None
    for idx, pattern in enumerate(patterns):
        if pattern is None:
            if current_pattern is not None:
                spans.append((start_idx, idx - 1, current_pattern))
                start_idx = None
                current_pattern = None
            continue
        if current_pattern is None:
            start_idx = idx
            current_pattern = pattern
            continue
        if pattern != current_pattern:
            spans.append((start_idx, idx - 1, current_pattern))
            start_idx = idx
            current_pattern = pattern

    if current_pattern is not None:
        spans.append((start_idx, len(patterns) - 1, current_pattern))

    for start_idx, end_idx, pattern in spans:
        hatch = "///" if pattern == "orifice" else "..."
        edgecolor = "#334155" if pattern == "orifice" else "#475569"
        ax.axvspan(
            start_idx - 0.5,
            end_idx + 0.5,
            facecolor=(1.0, 1.0, 1.0, 0.0),
            edgecolor=edgecolor,
            hatch=hatch,
            linewidth=0.0,
            alpha=alpha,
            zorder=0.1,
        )


def write_combined_png(
    out_path: Path,
    times,
    sim_series,
    analysis_series,
    smoother_series,
    min_q_series,
    flow_only_series,
    pf_series,
    pf_flow_only_series,
    nsga_series,
    nsga_flow_only_series,
    bayesopt_series,
    bayesopt_flow_only_series,
    obs_series,
    h2_sim_series,
    h2_enkf_series,
    h2_flow_only_series,
    h2_pf_series,
    h2_pf_flow_only_series,
    h2_nsga2_series,
    h2_nsga2_flow_only_series,
    h2_bayesopt_series,
    h2_bayesopt_flow_only_series,
    h2_obs_series,
    ensemble_series,
    best_member_index,
    q_base_series,
    q_analysis_series,
    q_min_q_series,
    q_flow_only_series,
    q_pf_series,
    q_pf_flow_only_series,
    q_nsga_series,
    q_nsga_flow_only_series,
    q_bayesopt_series,
    q_bayesopt_flow_only_series,
    q_control_gate_obs_series,
    q_target_gate_obs_series,
    q_control_gate_sim_series,
    q_target_gate_sim_series,
    e_control_gate_series,
    e_target_gate_series,
    e_target_gate_obs_series,
    mu_enkf_series,
    mu_enkf_smoother_series,
    mu_enkf_flow_only_series,
    mu_pf_series,
    mu_pf_flow_only_series,
    mu_nsga2_series,
    mu_nsga2_flow_only_series,
    mu_bayesopt_series,
    mu_bayesopt_flow_only_series,
    *,
    target_gate_name: str,
    upstream_gate_name: str,
    control_action_name: str,
    pool_segment_name: str,
    mu_gate_name: str,
    time_step_seconds_cache,
    ensemble_size,
    process_std,
    obs_std,
    init_spread,
    model_blend,
    feedback_gain,
    feedback_decay,
    mu_enkf_ensemble_series=None,
    mu_initial_value=None,
    ues_series_map=None,
    uef_series_map=None,
    ues_initial_value=None,
    uef_initial_value=None,
):
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    title_fontsize = 16
    label_fontsize = 13
    tick_fontsize = 11

    def _legend_under_title(ax, ncol: int, handles=None, labels=None) -> None:
        if handles is None or labels is None:
            handles, labels = ax.get_legend_handles_labels()
        if not handles:
            return
        ax.legend(
            handles=handles,
            labels=labels,
            loc="lower center",
            bbox_to_anchor=(0.5, 1.01),
            ncol=max(1, min(ncol, len(labels))),
            fontsize=10.5,
            frameon=True,
            framealpha=0.92,
            facecolor="white",
            edgecolor="#cbd5e1",
            borderpad=0.35,
            handlelength=1.8,
            columnspacing=1.2,
            labelspacing=0.35,
        )

    ues_series_map = ues_series_map or {}
    uef_series_map = uef_series_map or {}

    fig, axes = plt.subplots(7, 1, figsize=(18, 28), dpi=180)
    fig.patch.set_facecolor("#f8fafc")

    x = list(range(len(times)))
    segment_label = _display_name(pool_segment_name, f"{upstream_gate_name}-{target_gate_name}" if (upstream_gate_name or target_gate_name) else "", fallback=target_gate_name or "渠段")
    control_display_name = _display_name(control_action_name, upstream_gate_name, fallback="控制断面")
    target_display_name = _display_name(target_gate_name, fallback="目标断面")
    regime_series = _classify_gate_flow_regime(sim_series, h2_enkf_series)
    outflow_pattern_series = _classify_gate_outflow_pattern(sim_series, e_target_gate_series)

    for ax in axes:
        _render_regime_background(ax, regime_series)
        _render_outflow_pattern_background(ax, outflow_pattern_series)

    top = axes[0]
    top.set_facecolor("white")
    for idx, member in enumerate(ensemble_series):
        ys = [float(v) if v is not None else np.nan for v in member]
        if idx == best_member_index:
            top.plot(x, ys, color="#ea580c", linewidth=2.4, alpha=0.95, label="最接近实测的集合成员")
        else:
            top.plot(x, ys, color="#94a3b8", linewidth=0.9, alpha=0.35)

    top.plot(x, [float(v) if v is not None else np.nan for v in sim_series], color="#2563eb", linewidth=1.8, label="模型值")
    top.plot(x, [float(v) if v is not None else np.nan for v in analysis_series], color="#16a34a", linewidth=1.9, label="EnKF分析值")
    top.plot(x, [float(v) if v is not None else np.nan for v in smoother_series], color="#111827", linewidth=2.1, linestyle=":", label="EnKS平滑值")
    top.plot(x, [float(v) if v is not None else np.nan for v in min_q_series], color="#0f766e", linewidth=1.9, linestyle=(0, (5, 2)), label="EnKF最小分水修改")
    top.plot(x, [float(v) if v is not None else np.nan for v in flow_only_series], color="#7c3aed", linewidth=1.9, linestyle="--", label="仅初始水深修正,后续只改分水")
    if _has_series_data(pf_series):
        top.plot(x, [float(v) if v is not None else np.nan for v in pf_series], color="#0891b2", linewidth=1.9, label="PF逐步水深同化")
    if _has_series_data(pf_flow_only_series):
        top.plot(x, [float(v) if v is not None else np.nan for v in pf_flow_only_series], color="#a16207", linewidth=1.9, linestyle="--", label="PF仅初始水深修正,后续只改分水")
    if _has_series_data(nsga_series):
        top.plot(x, [float(v) if v is not None else np.nan for v in nsga_series], color="#dc2626", linewidth=1.9, label="NSGA-II逐步水深同化")
    if _has_series_data(nsga_flow_only_series):
        top.plot(x, [float(v) if v is not None else np.nan for v in nsga_flow_only_series], color="#0f766e", linewidth=1.9, linestyle="--", label="NSGA-II仅初始水深修正,后续只改分水")
    if _has_series_data(bayesopt_series):
        top.plot(x, [float(v) if v is not None else np.nan for v in bayesopt_series], color="#f59e0b", linewidth=1.9, label="贝叶斯优化逐步水深同化")
    if _has_series_data(bayesopt_flow_only_series):
        top.plot(x, [float(v) if v is not None else np.nan for v in bayesopt_flow_only_series], color="#475569", linewidth=1.9, linestyle="--", label="贝叶斯优化仅初始水深修正,后续只改分水")
    top.scatter(x, [float(v) if v is not None else np.nan for v in obs_series], color="#dc2626", s=16, label="实测值", zorder=4)
    top.grid(True, color="#e2e8f0", linewidth=0.8)
    top.set_ylabel("h1 (m)", fontsize=label_fontsize)
    top.set_title(f"{target_gate_name} 全集合水位过程线", fontsize=title_fontsize, pad=24, loc="left")
    _legend_under_title(top, ncol=4)

    h2_ax = axes[1]
    h2_ax.set_facecolor("white")
    h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_sim_series], color="#2563eb", linewidth=1.8, label="模型值")
    h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_enkf_series], color="#16a34a", linewidth=1.9, label="EnKF逐步同化分支模拟值")
    h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_flow_only_series], color="#7c3aed", linewidth=1.9, linestyle="--", label="EnKF仅初始水深修正,后续只改分水")
    if _has_series_data(h2_pf_series):
        h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_pf_series], color="#0891b2", linewidth=1.9, label="PF逐步水深同化分支模拟值")
    if _has_series_data(h2_pf_flow_only_series):
        h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_pf_flow_only_series], color="#a16207", linewidth=1.9, linestyle="--", label="PF仅初始水深修正,后续只改分水")
    if _has_series_data(h2_nsga2_series):
        h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_nsga2_series], color="#dc2626", linewidth=1.9, label="NSGA-II逐步水深同化分支模拟值")
    if _has_series_data(h2_nsga2_flow_only_series):
        h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_nsga2_flow_only_series], color="#0f766e", linewidth=1.9, linestyle="--", label="NSGA-II仅初始水深修正,后续只改分水")
    if _has_series_data(h2_bayesopt_series):
        h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_bayesopt_series], color="#f59e0b", linewidth=1.9, label="贝叶斯优化逐步水深同化分支模拟值")
    if _has_series_data(h2_bayesopt_flow_only_series):
        h2_ax.plot(x, [float(v) if v is not None else np.nan for v in h2_bayesopt_flow_only_series], color="#475569", linewidth=1.9, linestyle="--", label="贝叶斯优化仅初始水深修正,后续只改分水")
    h2_ax.scatter(x, [float(v) if v is not None else np.nan for v in h2_obs_series], color="#b91c1c", s=16, label="闸后实测值", zorder=4)
    h2_ax.grid(True, color="#e2e8f0", linewidth=0.8)
    h2_ax.set_ylabel("h2 (m)", fontsize=label_fontsize)
    h2_ax.set_title(f"{target_gate_name} 闸后水深过程线", fontsize=title_fontsize, pad=24, loc="left")
    _legend_under_title(h2_ax, ncol=4)

    bottom = axes[2]
    bottom.set_facecolor("white")
    bottom.scatter(x, [float(v) if v is not None else np.nan for v in q_base_series], color="#dc2626", s=16, label="边界原流量过程", zorder=4)
    bottom.plot(x, [float(v) if v is not None else np.nan for v in q_analysis_series], color="#16a34a", linewidth=1.9, label="EnKF逐步同化后流量")
    bottom.plot(x, [float(v) if v is not None else np.nan for v in q_min_q_series], color="#0f766e", linewidth=1.9, linestyle=(0, (5, 2)), label="EnKF最小分水修改流量")
    bottom.plot(x, [float(v) if v is not None else np.nan for v in q_flow_only_series], color="#7c3aed", linewidth=1.9, linestyle="--", label="EnKF仅初始修正后续只改分水")
    if _has_series_data(q_pf_series):
        bottom.plot(x, [float(v) if v is not None else np.nan for v in q_pf_series], color="#0891b2", linewidth=1.9, label="PF逐步同化后流量")
    if _has_series_data(q_pf_flow_only_series):
        bottom.plot(x, [float(v) if v is not None else np.nan for v in q_pf_flow_only_series], color="#a16207", linewidth=1.9, linestyle="--", label="PF仅初始修正后续只改分水")
    if _has_series_data(q_nsga_series):
        bottom.plot(x, [float(v) if v is not None else np.nan for v in q_nsga_series], color="#dc2626", linewidth=1.9, alpha=0.7, label="NSGA-II逐步同化后流量")
    if _has_series_data(q_nsga_flow_only_series):
        bottom.plot(x, [float(v) if v is not None else np.nan for v in q_nsga_flow_only_series], color="#0f766e", linewidth=1.9, linestyle="--", label="NSGA-II仅初始修正后续只改分水")
    if _has_series_data(q_bayesopt_series):
        bottom.plot(x, [float(v) if v is not None else np.nan for v in q_bayesopt_series], color="#f59e0b", linewidth=1.9, label="贝叶斯优化逐步同化后流量")
    if _has_series_data(q_bayesopt_flow_only_series):
        bottom.plot(x, [float(v) if v is not None else np.nan for v in q_bayesopt_flow_only_series], color="#475569", linewidth=1.9, linestyle="--", label="贝叶斯优化仅初始修正后续只改分水")
    bottom.grid(True, color="#e2e8f0", linewidth=0.8)
    bottom.set_ylabel("流量", fontsize=label_fontsize)
    bottom.set_xlabel("时间步", fontsize=label_fontsize)
    bottom.set_title("原流量过程与同化后流量过程", fontsize=title_fontsize, pad=24, loc="left")
    _legend_under_title(bottom, ncol=4)

    gate_flow = axes[3]
    gate_flow.set_facecolor("white")
    key_flow_sum_sim_series = _signed_flow_sum_series([q_control_gate_sim_series], [q_target_gate_sim_series])
    key_flow_sum_obs_series = _signed_flow_sum_series([q_control_gate_obs_series], [q_target_gate_obs_series])
    gate_flow.plot(x, [float(v) if v is not None else np.nan for v in q_base_series], color="#1f2937", linewidth=1.7, linestyle=(0, (4, 2)), label="边界给定流量")
    gate_flow.plot(x, [float(v) if v is not None else np.nan for v in q_control_gate_sim_series], color="#0f766e", linewidth=2.0, label=f"{control_display_name}模拟流量")
    gate_flow.scatter(x, [float(v) if v is not None else np.nan for v in q_control_gate_obs_series], color="#14b8a6", s=16, label=f"{control_display_name}实测流量", zorder=4)
    gate_flow.plot(x, [float(v) if v is not None else np.nan for v in q_target_gate_sim_series], color="#b91c1c", linewidth=2.0, label=f"{target_display_name}模拟流量")
    gate_flow.scatter(x, [float(v) if v is not None else np.nan for v in q_target_gate_obs_series], color="#fb7185", s=16, label=f"{target_display_name}实测流量", zorder=4)
    gate_flow.plot(x, [float(v) if v is not None else np.nan for v in key_flow_sum_sim_series], color="#7c3aed", linewidth=2.2, linestyle="-.", label="关键断面净流量(模拟, 入正出负)")
    if _has_series_data(key_flow_sum_obs_series):
        gate_flow.scatter(x, [float(v) if v is not None else np.nan for v in key_flow_sum_obs_series], color="#a855f7", s=16, label="关键断面净流量(实测, 入正出负)", zorder=4)
    gate_flow.grid(True, color="#e2e8f0", linewidth=0.8)
    gate_flow.set_ylabel("流量", fontsize=label_fontsize)
    gate_flow.set_title(f"{segment_label} 关键流量过程", fontsize=title_fontsize, pad=24, loc="left")
    _legend_under_title(gate_flow, ncol=4)

    gate_opening = axes[4]
    gate_opening.set_facecolor("white")
    gate_opening_obs = gate_opening.twinx()
    gate_opening.plot(x, [float(v) if v is not None else np.nan for v in e_control_gate_series], color="#0891b2", linewidth=1.8, linestyle="--", label=f"{control_display_name}模拟开度")
    gate_opening.plot(x, [float(v) if v is not None else np.nan for v in e_target_gate_series], color="#f59e0b", linewidth=1.9, label=f"{target_display_name}模拟开度")
    gate_opening_obs.scatter(x, [float(v) if v is not None else np.nan for v in e_target_gate_obs_series], color="#b91c1c", s=18, label=f"{target_display_name}实测开度", zorder=4)
    gate_opening.grid(True, color="#e2e8f0", linewidth=0.8)
    gate_opening.set_ylabel("模拟开度", fontsize=label_fontsize)
    gate_opening_obs.set_ylabel("实测开度", fontsize=label_fontsize)
    gate_opening.set_xlabel("时间步", fontsize=label_fontsize)
    gate_opening.set_title(f"{segment_label} 关键闸门开度过程", fontsize=title_fontsize, pad=24, loc="left")
    gate_opening.tick_params(axis="y", colors="#0f172a")
    gate_opening_obs.tick_params(axis="y", colors="#b91c1c")
    obs_handles, obs_labels = gate_opening_obs.get_legend_handles_labels()
    model_handles, model_labels = gate_opening.get_legend_handles_labels()
    _legend_under_title(gate_opening, ncol=4, handles=model_handles + obs_handles, labels=model_labels + obs_labels)

    mu_ax = axes[5]
    mu_ax.set_facecolor("white")
    if mu_enkf_ensemble_series:
        for idx, member in enumerate(mu_enkf_ensemble_series):
            ys = [float(v) if v is not None else np.nan for v in member]
            mu_ax.plot(x, ys, color="#a8a29e", linewidth=0.9, alpha=0.5, label="EnKF mu成员" if idx == 0 else None)
    if mu_initial_value is not None:
        mu_ax.axhline(float(mu_initial_value), color="#dc2626", linewidth=1.5, linestyle=(0, (3, 3)), label="EnKF mu初值")
    mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_enkf_series], color="#111827", linewidth=2.2, label="EnKF mu均值")
    mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_enkf_smoother_series], color="#16a34a", linewidth=2.0, linestyle=":", label="EnKS mu平滑值")
    mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_enkf_flow_only_series], color="#7c3aed", linewidth=1.9, linestyle="--", label="EnKF仅初始水深修正分支 mu")
    if _has_series_data(mu_pf_series):
        mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_pf_series], color="#0891b2", linewidth=1.9, label="PF mu")
    if _has_series_data(mu_pf_flow_only_series):
        mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_pf_flow_only_series], color="#a16207", linewidth=1.9, linestyle="--", label="PF仅初始水深修正分支 mu")
    if _has_series_data(mu_nsga2_series):
        mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_nsga2_series], color="#dc2626", linewidth=1.9, label="NSGA-II mu")
    if _has_series_data(mu_nsga2_flow_only_series):
        mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_nsga2_flow_only_series], color="#0f766e", linewidth=1.9, linestyle="--", label="NSGA-II仅初始水深修正分支 mu")
    if _has_series_data(mu_bayesopt_series):
        mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_bayesopt_series], color="#f59e0b", linewidth=1.9, label="贝叶斯优化 mu")
    if _has_series_data(mu_bayesopt_flow_only_series):
        mu_ax.plot(x, [float(v) if v is not None else np.nan for v in mu_bayesopt_flow_only_series], color="#475569", linewidth=1.9, linestyle="--", label="贝叶斯优化仅初始水深修正分支 mu")
    mu_ax.grid(True, color="#e2e8f0", linewidth=0.8)
    mu_ax.set_ylabel("均值参数", fontsize=label_fontsize)
    mu_ax.set_title(f"{mu_gate_name} 平均闸门参数过程", fontsize=title_fontsize, pad=24, loc="left")
    _legend_under_title(mu_ax, ncol=4)

    param_ax = axes[6]
    param_ax.set_facecolor("white")
    if ues_initial_value is not None:
        param_ax.axhline(float(ues_initial_value), color="#dc2626", linewidth=1.3, linestyle=(0, (3, 3)), label="Ues初值")
    if uef_initial_value is not None:
        param_ax.axhline(float(uef_initial_value), color="#0f766e", linewidth=1.3, linestyle=(0, (3, 3)), label="Uef初值")
    if _has_series_data(ues_series_map.get("enkf")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in ues_series_map.get("enkf", [])], color="#111827", linewidth=2.2, label="EnKF Ues")
    if _has_series_data(uef_series_map.get("enkf")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in uef_series_map.get("enkf", [])], color="#111827", linewidth=2.0, linestyle="--", label="EnKF Uef")
    if _has_series_data(ues_series_map.get("enkf_smoother")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in ues_series_map.get("enkf_smoother", [])], color="#16a34a", linewidth=2.0, linestyle=":", label="EnKS Ues")
    if _has_series_data(uef_series_map.get("enkf_smoother")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in uef_series_map.get("enkf_smoother", [])], color="#16a34a", linewidth=2.0, linestyle=(0, (3, 2, 1, 2)), label="EnKS Uef")
    if _has_series_data(ues_series_map.get("enkf_flow_only")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in ues_series_map.get("enkf_flow_only", [])], color="#7c3aed", linewidth=1.9, label="EnKF flow-only Ues")
    if _has_series_data(uef_series_map.get("enkf_flow_only")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in uef_series_map.get("enkf_flow_only", [])], color="#7c3aed", linewidth=1.9, linestyle="--", label="EnKF flow-only Uef")
    if _has_series_data(ues_series_map.get("pf")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in ues_series_map.get("pf", [])], color="#0891b2", linewidth=1.9, label="PF Ues")
    if _has_series_data(uef_series_map.get("pf")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in uef_series_map.get("pf", [])], color="#0891b2", linewidth=1.9, linestyle="--", label="PF Uef")
    if _has_series_data(ues_series_map.get("pf_flow_only")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in ues_series_map.get("pf_flow_only", [])], color="#a16207", linewidth=1.9, label="PF flow-only Ues")
    if _has_series_data(uef_series_map.get("pf_flow_only")):
        param_ax.plot(x, [float(v) if v is not None else np.nan for v in uef_series_map.get("pf_flow_only", [])], color="#a16207", linewidth=1.9, linestyle="--", label="PF flow-only Uef")
    param_ax.grid(True, color="#e2e8f0", linewidth=0.8)
    param_ax.set_ylabel("Ues/Uef", fontsize=label_fontsize)
    param_ax.set_xlabel("时间步", fontsize=label_fontsize)
    param_ax.set_title(f"{mu_gate_name} Ues/Uef 同化过程", fontsize=title_fontsize, pad=24, loc="left")
    _legend_under_title(param_ax, ncol=4)

    if times:
        tick_idx = sorted({0, max(0, len(times) // 4), max(0, len(times) // 2), max(0, (3 * len(times)) // 4), max(0, len(times) - 1)})
        tick_labels = [times[idx] for idx in tick_idx]
        param_ax.set_xticks(tick_idx)
        param_ax.set_xticklabels(tick_labels, rotation=12, ha="right", fontsize=tick_fontsize)

    for ax in axes:
        ax.tick_params(axis="both", labelsize=tick_fontsize)

    fig.suptitle(
        f"步长={time_step_seconds_cache}s  集合数={ensemble_size}  过程噪声={process_std}  观测噪声={obs_std}  初始扰动={init_spread}\n"
        f"模型混合={model_blend}  反馈增益={feedback_gain}  反馈衰减={feedback_decay}  蓝底=自由流  红底=淹没流",
        fontsize=12,
        y=0.968,
    )
    fig.tight_layout(rect=(0.03, 0.03, 0.98, 0.975), h_pad=1.9)
    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


def write_enkf_initial_comparison_png(
    out_path: Path,
    times,
    baseline_series,
    baseline_h2_series,
    baseline_target_gate_opening_series,
    obs_series,
    baseline_q_series,
    initial_ues_value,
    initial_uef_value,
    replay_series,
    replay_q_series,
    replay_rmse,
    replay_with_q_series,
    replay_with_q_q_series,
    replay_with_q_rmse,
    *,
    target_gate_name: str,
):
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    title_fontsize = 16
    label_fontsize = 13
    tick_fontsize = 11

    def _legend_above(ax, ncol: int) -> None:
        handles, labels = ax.get_legend_handles_labels()
        if not handles:
            return
        ax.legend(
            handles,
            labels,
            loc="lower center",
            bbox_to_anchor=(0.5, 1.01),
            ncol=max(1, min(ncol, len(labels))),
            fontsize=10.5,
            frameon=True,
            framealpha=0.92,
            facecolor="white",
            edgecolor="#cbd5e1",
            borderpad=0.35,
            columnspacing=1.2,
            labelspacing=0.35,
        )

    x = list(range(len(times)))
    sim_values = [float(v) if v is not None else np.nan for v in baseline_series]
    replay_values = [float(v) if v is not None else np.nan for v in replay_series]
    replay_with_q_values = [float(v) if v is not None else np.nan for v in replay_with_q_series]
    obs_values = [float(v) if v is not None else np.nan for v in obs_series]
    baseline_q_values = [float(v) if v is not None else np.nan for v in baseline_q_series]
    replay_q_values = [float(v) if v is not None else np.nan for v in replay_q_series]
    replay_with_q_q_values = [float(v) if v is not None else np.nan for v in replay_with_q_q_series]

    fig, axes = plt.subplots(2, 1, figsize=(18, 10), dpi=180, sharex=True)
    fig.patch.set_facecolor("#f8fafc")
    regime_series = _classify_gate_flow_regime(baseline_series, baseline_h2_series)
    outflow_pattern_series = _classify_gate_outflow_pattern(baseline_series, baseline_target_gate_opening_series)

    for ax in axes:
        _render_regime_background(ax, regime_series)
        _render_outflow_pattern_background(ax, outflow_pattern_series)

    ues_text = "None" if initial_ues_value is None else f"{float(initial_ues_value):.4f}"
    uef_text = "None" if initial_uef_value is None else f"{float(initial_uef_value):.4f}"
    fig.suptitle(
        f"{target_gate_name} EnKF初始条件重演对比\n初始Ues={ues_text}  初始Uef={uef_text}  蓝底=自由流  红底=淹没流",
        fontsize=16,
        y=0.965,
    )

    top = axes[0]
    top.set_facecolor("white")
    top.plot(x, sim_values, color="#2563eb", linewidth=1.8, label="模型值")
    top.plot(x, replay_values, color="#7c3aed", linewidth=1.9, linestyle="--", label="EnKF初始状态和参数重演")
    top.plot(x, replay_with_q_values, color="#16a34a", linewidth=1.9, label="EnKF初始状态、参数和分水重演")
    top.scatter(x, obs_values, color="#dc2626", s=16, label="实测值", zorder=4)
    top.grid(True, color="#e2e8f0", linewidth=0.8)
    top.set_ylabel("h1 (m)", fontsize=label_fontsize)
    top.set_title(f"{target_gate_name} 水位过程对比", fontsize=title_fontsize, pad=24, loc="left")
    rmse_replay_txt = "None" if replay_rmse is None else f"{replay_rmse:.4f}"
    rmse_replay_with_q_txt = "None" if replay_with_q_rmse is None else f"{replay_with_q_rmse:.4f}"
    top.text(
        0.01,
        0.98,
        f"初始状态+参数 RMSE={rmse_replay_txt}    初始状态+参数+分水 RMSE={rmse_replay_with_q_txt}",
        transform=top.transAxes,
        ha="left",
        va="top",
        fontsize=11,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "#f8fafc", "edgecolor": "#cbd5e1", "alpha": 0.95},
    )
    _legend_above(top, ncol=4)

    bottom = axes[1]
    bottom.set_facecolor("white")
    bottom.scatter(x, baseline_q_values, color="#dc2626", s=16, label="原流量过程", zorder=4)
    bottom.plot(x, replay_q_values, color="#7c3aed", linewidth=1.9, linestyle="--", label="EnKF初始状态和参数重演所用流量")
    bottom.plot(x, replay_with_q_q_values, color="#16a34a", linewidth=1.9, label="EnKF初始状态、参数和分水重演所用流量")
    bottom.grid(True, color="#e2e8f0", linewidth=0.8)
    bottom.set_ylabel("流量", fontsize=label_fontsize)
    bottom.set_xlabel("时间步", fontsize=label_fontsize)
    bottom.set_title("原流量过程与重演流量过程", fontsize=title_fontsize, pad=24, loc="left")
    _legend_above(bottom, ncol=3)

    if times:
        tick_idx = sorted({0, max(0, len(times) // 4), max(0, len(times) // 2), max(0, (3 * len(times)) // 4), max(0, len(times) - 1)})
        tick_labels = [times[idx] for idx in tick_idx]
        bottom.set_xticks(tick_idx)
        bottom.set_xticklabels(tick_labels, rotation=12, ha="right", fontsize=tick_fontsize)

    for ax in axes:
        ax.tick_params(axis="both", labelsize=tick_fontsize)

    fig.tight_layout(rect=(0.03, 0.03, 0.98, 0.97), h_pad=1.9)
    fig.savefig(out_path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
