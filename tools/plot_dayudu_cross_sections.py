"""Plot applied U-section profiles and hydraulic area tables against old mesh."""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
case=ROOT/'data/dayudu'
root=json.loads((case/'input/CrossSection_dayudu_hydraulic_tables.geojson').read_text(encoding='utf-8'))
verification=json.loads((case/'output/cross_section_lookup/node_geometry_before_after.json').read_text(encoding='utf-8'))
features=[f['properties'] for f in root['features'] if f['properties']['section_type']=='u_shaped']
fig, axes=plt.subplots(1,2,figsize=(12,4.8))
for p in features:
    points=np.array(p['local_cross_section']['points_xz'])
    axes[0].plot(points[:,0]-points[:,0].max()/2,points[:,1],label=f"{p['Name']}: {p['chainage']}")
    rows=np.array(p['hydraulic_lookup']['rows'])
    axes[1].plot(rows[:,0],rows[:,1],label=f"{p['Name']} lookup")
node=features[0]['model_node_ids'][0]
old_width=verification['before']['node_Width'][node]
old_slope=verification['before']['node_tanb'][node]
height=features[0]['channel_height_m']
depth=np.linspace(0,height,301)
axes[1].plot(depth,old_width*depth+old_slope*depth**2,'k--',label=f'Previous mesh at node {node}')
axes[0].set(xlabel='Local transverse distance from centre (m)',ylabel='Height above bed (m)',title='Applied circular-bottom U sections')
axes[0].set_aspect('equal',adjustable='box')
axes[1].set(xlabel='Water depth (m)',ylabel='Wetted area (m²)',title='Hydraulic area used by the solver')
for ax in axes:
    ax.grid(alpha=.25);ax.legend(fontsize=8)
fig.suptitle('Dayudu section lookup: derived profiles, exact stake association; original bed preserved',fontsize=11)
fig.tight_layout()
output=case/'output/cross_section_lookup/u_sections_and_area.png'
fig.savefig(output,dpi=180)
print(output)
