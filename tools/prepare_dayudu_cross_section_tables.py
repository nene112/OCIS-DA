"""Generate auditable open-channel hydraulic tables and exact stake associations.

No surveyed XYZ is inferred. Closed tunnels/pipes and incomplete sections retain
the existing model geometry and are listed explicitly in the output report.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def geometry(props):
    height, width = props.get('channel_height_m'), props.get('top_width_m')
    kind = props.get('geometry_approximation_type', props['section_type'])
    if props.get('structure_type') in ('tunnel', 'culvert', 'pipe'):
        raise ValueError('closed structure: free-surface lookup cannot replace pressure-flow geometry')
    measured = props.get('local_cross_section') or {}
    if measured.get('is_measured_profile') and measured.get('points_xz'):
        points = measured['points_xz']
        bed = min(z for x, z in points)
        bed_x = [x for x, z in points if z == bed]
        return points, max(bed_x)-min(bed_x), 0.0, ['measured local profile takes precedence over regular dimensions']
    if not height or not width:
        raise ValueError('missing channel height or mouth width')
    notes = []
    if kind in ('rectangle', 'trapezoid'):
        slope = 0.0 if kind == 'rectangle' else props.get('side_slope')
        if slope is None or slope < 0:
            raise ValueError('missing side slope')
        bottom = width - 2*slope*height
        if bottom <= 0:
            raise ValueError('nonpositive bottom width inferred from mouth width, height and slope')
        return [[0, height], [slope*height, 0], [slope*height+bottom, 0], [width, height]], bottom, slope, notes
    if kind not in ('u_shaped', 'arc_bottom_trapezoid'):
        points = (props.get('local_cross_section') or {}).get('points_xz')
        if points:
            return points, 0.0, 0.0, ['external local profile takes precedence']
        raise ValueError('no supported geometry or measured local profile')
    radius = props.get('radius_m') or ((props.get('diameter_m') or 0)/2)
    if not radius or radius <= 0:
        raise ValueError('missing positive radius (D-prefixed value must be converted to radius)')
    if kind == 'u_shaped':
        angle = props.get('inclination_deg')
        if angle is None or not 0 <= angle < 90:
            raise ValueError('missing U-section wall inclination')
        slope = math.tan(math.radians(angle))
        notes.append('U wall inclination is measured from vertical; D is diameter')
    else:
        slope = props.get('side_slope')
        if slope is None or slope < 0:
            raise ValueError('missing arc-section tangent side slope; no inheritance from adjacent row')
    alpha = math.atan2(1.0, slope)
    join_h = radius*(1-math.cos(alpha))
    join_x = radius*math.sin(alpha)
    if height < join_h:
        alpha = math.acos(1-height/radius)
        join_h, join_x = height, radius*math.sin(alpha)
    half_width = join_x+slope*(height-join_h)
    arc = [[radius*math.sin(t)+half_width, radius*(1-math.cos(t))]
           for t in [(-alpha+2*alpha*i/256) for i in range(257)]]
    points = [[0, height], *arc, [2*half_width, height]] if height > join_h else arc
    match = re.search(r'h\s*\+\s*([0-9.]+)', props.get('engineering_measures') or '')
    raised = float(match.group(1)) if match else 0.0
    ref_h = height-raised
    if ref_h >= join_h:
        predicted = 2*(join_x+slope*(ref_h-join_h))
    else:
        predicted = 2*math.sqrt(max(0, 2*radius*ref_h-ref_h*ref_h))
    error = abs(predicted-width)/width
    notes.append(f'mouth width checked at height minus documented raise ({raised:g} m): predicted={predicted:.6f}, relative_error={error:.6%}')
    if error > .02:
        notes.append('DIMENSION_CONFLICT: supplied radius/slope govern geometry; mouth width differs by >2%; engineering review required')
    return points, 0.0, slope, notes


def make_table(points):
    if len(points) < 3 or any(not all(math.isfinite(v) for v in p) for p in points):
        raise ValueError('invalid local profile')
    if any(b[0] < a[0] for a, b in zip(points, points[1:])):
        raise ValueError('local profile x must be nondecreasing')
    min_z = min(z for x, z in points)
    points = [[x, z-min_z] for x, z in points]
    height = min(points[0][1], points[-1][1])
    if height <= 0:
        raise ValueError('profile banks must be higher than bed')
    # Include every vertex elevation: width and perimeter are piecewise linear
    # between these heights. Additional samples make diagnostics easy to plot.
    depths = sorted({0.0, height, *(z for x, z in points if 0 < z < height),
                     *(height*i/1000 for i in range(1, 1000))})
    rows = []
    area = pressure = 0.0
    for h in depths:
        width = perimeter = 0.0
        for (x0, z0), (x1, z1) in zip(points, points[1:]):
            dx, dz = x1-x0, z1-z0
            if dz == 0:
                fraction = 1.0 if h >= z0 else 0.0
            else:
                fraction = min(1.0, max(0.0, (h-min(z0, z1))/abs(dz)))
            width += dx*fraction
            perimeter += math.hypot(dx, dz)*fraction
        if rows:
            previous = rows[-1]; delta = h-previous[0]
            pressure += previous[1]*delta+previous[2]*delta**2/2+(width-previous[2])*delta**2/6
            area += (previous[2]+width)*delta/2
        rows.append([h, area, width, perimeter, pressure])
    return points, rows


def prepare(case: Path, output: Path):
    original = json.loads((case/'input/CrossSection_dayudu_summary.geojson').read_text(encoding='utf-8-sig'))
    prepared = copy.deepcopy(original)
    by_name = {f['properties']['Name']: f['properties'] for f in prepared['features']}
    # Only the two explicitly authorized incomplete open-channel records.
    # Keep original CSV values and section classification for provenance.
    for target, donor, fields, approximation in [
        ('DYD_003', 'DYD_002', ['side_slope'], None),
        ('DYD_034', 'DYD_033', ['channel_height_m', 'top_width_m', 'side_slope'], 'rectangle'),
    ]:
        props, source = by_name[target], by_name[donor]
        inherited = {}
        for field in fields:
            if props.get(field) is None:
                value = source.get(field)
                if value is None:
                    raise ValueError(f'{donor} has no usable {field} for {target}')
                inherited[field] = {'original_value': None, 'adopted_value': value}
                props[field] = value
        if inherited:
            props['parameter_imputation'] = {'source_section': donor,
                'source_chainage': source['chainage'], 'fields': inherited,
                'method': 'nearest adjoining upstream open-channel section',
                'is_measured': False, 'authorized_scope': 'fill incomplete open-channel sections from similar sections'}
            if approximation:
                props['geometry_approximation_type'] = approximation
    stakes = json.loads((case/'mesh/stake.json').read_text(encoding='utf-8-sig'))['总干渠']
    assignments = sorted((float(station), int(node)) for station, node in stakes.items())
    report = []
    for feature in prepared['features']:
        props = feature['properties']
        props['model_canal'] = '总干渠'
        props['model_node_ids'] = [node for station, node in assignments
                                 if props['chainage_start_m'] <= station < props['chainage_end_m']
                                 or (station == props['chainage_end_m'] == 30750)]
        try:
            points, bottom, slope, notes = geometry(props)
            if props.get('parameter_imputation'):
                notes.append('IMPUTED_PARAMETERS: borrowed from '+props['parameter_imputation']['source_section']+'; not surveyed; transition uses constant donor geometry where applicable')
            points, rows = make_table(points)
            props['hydraulic_lookup'] = {'columns': ['h', 'A', 'T', 'P', 'I'], 'rows': rows,
                'overflow_policy': 'vertical_wall_extension', 'closed': False}
            is_measured = (props.get('local_cross_section') or {}).get('is_measured_profile', False)
            props['local_cross_section'] = {'points_xz': points, 'is_measured_profile': is_measured,
                'method': 'measured profile' if is_measured else 'derived from explicitly supplied dimensions; tangent circular arc for U/arc sections'}
            props['model_bottom_width_m'], props['model_side_slope'] = bottom, slope
            props['lookup_status'] = 'ready_with_warning' if props.get('parameter_imputation') or any('DIMENSION_CONFLICT' in n for n in notes) else 'ready'
            props['lookup_notes'] = notes
        except ValueError as exc:
            props['hydraulic_lookup'] = None
            props['lookup_status'], props['lookup_notes'] = 'unresolved', [str(exc)]
        report.append({'name': props['Name'], 'chainage': props['chainage'], 'type': props['section_type'],
            'status': props['lookup_status'], 'nodes': len(props['model_node_ids']), 'notes': props['lookup_notes'],
            'parameter_imputation': props.get('parameter_imputation')})
    prepared['metadata'].update({'format': 'ocis_section_tables_v1',
        'association': 'exact mesh/stake.json 总干渠 station-to-node mapping; no XY-distance approximation',
        'geometry_status': 'local cross-section only; original model bed elevations preserved',
        'dll_compatibility': 'extended native set_CrossSection reads hydraulic_lookup and exact node associations',
        'overflow_policy': 'explicit vertical wall extension above table height; report exceedances',
        'unresolved_policy': 'retain existing model geometry and report incomplete/closed sections'})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(prepared, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    report_path = output.with_suffix('.report.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path, default=ROOT/'data/dayudu')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.case/'input/CrossSection_dayudu_hydraulic_tables.geojson'
    report = prepare(args.case, output)
    print(json.dumps({'output': str(output), 'ready': sum(r['status'].startswith('ready') for r in report),
        'unresolved': [r['name'] for r in report if r['status'] == 'unresolved']}, ensure_ascii=False))
