"""Import dayudu hydraulic section tables and verify actual node geometry."""
from __future__ import annotations
import argparse, hashlib, json, math, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools'), str(ROOT/'tools/GPT-Reconstruction')]
import HD_Roe
import reach_missing_data_assimilation as da
from prepare_dayudu_cross_section_tables import prepare

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, default=ROOT/'data/dayudu')
    parser.add_argument('--dll', type=Path, default=ROOT/'tools/OcisMILPNet.dll')
    args = parser.parse_args()
    case = args.case.resolve()
    source = case/'input/CrossSection_dayudu_hydraulic_tables.geojson'
    sections = prepare(case, source)
    config = da.AssimilationConfig(initial_water_depth=1.61)
    runtime = json.loads(da._build_runtime_config(case/'OCIS_dataConfig.json', case, da.discover_reaches(case)[0], config))
    runtime['SIM'].pop('cross_section_path', None)
    client = da._load_client(json.dumps(runtime), config, lambda: HD_Roe.create_client(args.dll))
    client.set_outputfile_Label(0)
    fields = ['node_zb', 'node_h', 'node_wh', 'node_Width', 'node_tanb', 'node_section_id', 'node_A', 'node_B', 'node_P', 'node_HR']
    read = lambda: {key: json.loads(client.get_stepdata_sim(key)) for key in fields}
    before = read()
    client.set_CrossSection(source)
    after = read()
    assert before['node_zb'] == after['node_zb']
    assert before['node_h'] == after['node_h'] and before['node_wh'] == after['node_wh']
    assert before['node_Width'] != after['node_Width'] and before['node_tanb'] != after['node_tanb']
    ids = after['node_section_id']
    assert any(ids)
    for i, section in enumerate(ids):
        if section:
            assert all(math.isfinite(after[key][i]) for key in fields)
            assert after['node_A'][i] > 0 and after['node_B'][i] > 0 and after['node_P'][i] > 0
            assert abs(after['node_HR'][i]-after['node_A'][i]/after['node_P'][i]) < 1e-10
    report = client.get_cross_section_report()
    report.update({'input': str(source), 'dll_sha256': hashlib.sha256(args.dll.read_bytes()).hexdigest(),
        'width_modified': True, 'slope_modified': True, 'bed_preserved': True,
        'u_sections_loaded': [r['name'] for r in sections if r['type']=='u_shaped' and r['status'].startswith('ready')]})
    out = case/'output/cross_section_lookup'
    out.mkdir(parents=True, exist_ok=True)
    (out/'import_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (out/'node_geometry_before_after.json').write_text(json.dumps({'before': before, 'after': after}), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
