"""Read the dayudu summary through the native cross-section interface.

Missing positions are supplied in a separate, explicitly model-derived file.
This imports elevations only, not channel shape parameters.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tools/GPT-Reconstruction')]
import HD_Roe
import reach_missing_data_assimilation as da


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, default=ROOT / 'data/dayudu')
    parser.add_argument('--dll', type=Path, default=ROOT / 'tools/OcisMILPNet.dll')
    args = parser.parse_args()
    case = args.case.resolve()
    source = case / 'input/CrossSection_dayudu_summary.geojson'
    out = case / 'output/cross_section_import_manual_no_waga'
    out.mkdir(parents=True, exist_ok=True)
    original = json.loads(source.read_text(encoding='utf-8'))
    prepared = copy.deepcopy(original)

    nodes = {}
    for line in (case / 'mesh/input.txt').read_text(encoding='utf-8-sig').splitlines():
        fields = line.split()
        if len(fields) >= 5:
            nodes[int(fields[0])] = tuple(float(v) for v in fields[1:4])
    links = {}
    for line in (case / 'mesh/neighborId.txt').read_text().splitlines():
        if line and not line.startswith(';'):
            fields = line.split()
            links[int(fields[0])] = int(fields[3])
    route, seen = [], set()
    index = 0
    while index in nodes and index not in seen:
        route.append(index)
        seen.add(index)
        index = links.get(index, -1)
    distances = [0.0]
    for a, b in zip(route, route[1:]):
        distances.append(distances[-1] + math.hypot(nodes[b][0] - nodes[a][0], nodes[b][1] - nodes[a][1]))
    assert distances[-1] >= max(f['properties']['chainage_start_m'] for f in prepared['features'])
    for feature in prepared['features']:
        props = feature['properties']
        station = props['chainage_start_m']
        nearest = min(range(len(route)), key=lambda i: abs(distances[i] - station))
        node = route[nearest]
        xyz = list(nodes[node])
        feature['geometry'] = {'type': 'Point', 'coordinates': xyz}
        props.update(dict(zip(('x', 'y', 'z'), xyz)))
        props['geometry_provenance'] = {
            'source': 'mesh/input.txt and mesh/neighborId.txt',
            'node_id': node,
            'method': 'nearest node by cumulative planar distance along next-node route from node 0',
            'model_distance_m': distances[nearest],
            'distance_error_m': distances[nearest] - station,
            'is_measured_csv_coordinate': False,
            'limitations': 'Approximate chainage association; mesh route length differs from table coverage. Model elevation, not a new survey.'
        }
    prepared['metadata'].update({
        'geometry_status': 'Model-derived XYZ in a separate import file; original summary unchanged',
        'dll_compatibility': 'geometry.coordinates is populated for native elevation reader; shape arrays remain unused',
        'model_route_length_m': distances[-1],
        'source_table_length_m': 30750,
        'purpose': 'Native JSON reading verification, not measured-section calibration',
    })
    compatible = out / 'CrossSection_dayudu_model_geometry.geojson'
    compatible.write_text(json.dumps(prepared, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    config = da.AssimilationConfig(initial_water_depth=1.61)
    reach = da.discover_reaches(case)[0]
    runtime = da._build_runtime_config(case / 'OCIS_dataConfig.json', case, reach, config)
    client = da._load_client(runtime, config, lambda: HD_Roe.create_client(args.dll))
    client.set_outputfile_Label(0)
    client.Auto_correct_endpoint()
    client.Auto_correct_gates_calculationType()

    def values(name):
        data = json.loads(client.get_stepdata_sim(name))
        return {str(i): v for i, v in enumerate(data)} if isinstance(data, list) else data

    before = {name: values(name) for name in ['node_zb', 'node_h', 'node_wh', 'node_Width', 'node_tanb']}
    try:
        client.set_CrossSection(source)
    except ValueError as exc:
        missing_rejected = str(exc)
    else:
        raise AssertionError('Missing XYZ must not be silently accepted')
    client.set_CrossSection(compatible, 0, len(prepared['features']) - 1)
    after = {name: values(name) for name in before}
    changed = [key for key in before['node_zb'] if abs(after['node_zb'][key] - before['node_zb'][key]) > 1e-9]
    assert changed, 'Native import did not affect any elevation'
    assert before['node_h'] == after['node_h'], 'Depth should remain unchanged during elevation import'
    assert before['node_Width'] == after['node_Width'] and before['node_tanb'] == after['node_tanb'], 'This native reader should not change channel shape'
    for key, z in after['node_zb'].items():
        assert math.isfinite(z)
        assert abs(after['node_wh'][key] - z - after['node_h'][key]) < 1e-8
    report = {
        'dll': str(args.dll.resolve()),
        'dll_sha256': hashlib.sha256(args.dll.read_bytes()).hexdigest(),
        'input': str(compatible),
        'csv_records': len(prepared['features']),
        'native_function': 'set_CrossSection',
        'section_range': [0, len(prepared['features']) - 1],
        'model_nodes': len(before['node_zb']),
        'changed_bed_nodes': len(changed),
        'depth_preserved': True,
        'channel_width_and_side_slope_preserved': True,
        'original_incomplete_json_rejected': missing_rejected,
        'geometry_source': 'existing model mesh; not measured CSV geometry',
        'model_route_length_m': distances[-1],
        'table_coverage_m': 30750,
        'native_time_parameters': json.loads(client.get_time_param()),
        'scope': 'Import verification only; no reconstruction or hydraulic step run'
    }
    (out / 'import_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'node_elevation_before_after.json').write_text(json.dumps({'before': before['node_zb'], 'after': after['node_zb']}), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
