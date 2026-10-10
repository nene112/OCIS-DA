"""Compare actual pre-import mesh dimensions with applied JSON section profiles."""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.backends.backend_pdf import PdfPages

ROOT=Path(__file__).resolve().parents[1]
case=ROOT/'data/dayudu'
out=case/'output/cross_section_lookup'
root=json.loads((case/'input/CrossSection_dayudu_hydraulic_tables.geojson').read_text(encoding='utf-8'))
data=json.loads((out/'node_geometry_before_after.json').read_text(encoding='utf-8'))
font_path=Path('C:/Windows/Fonts/msyh.ttc')
if font_path.exists():
    plt.rcParams['font.family']=FontProperties(fname=str(font_path)).get_name()
plt.rcParams['axes.unicode_minus']=False
features={f['properties']['Name']:f['properties'] for f in root['features']}
colours={'before':'#64748b','after':'#e76f26'}

def section_values(p):
    ids=p['model_node_ids'];node=ids[len(ids)//2]
    assert data['before']['node_section_id'][node]==0
    assert data['after']['node_section_id'][node]>0
    b=data['before']['node_Width'][node];m=data['before']['node_tanb'][node]
    height=p['channel_height_m']
    old=np.array([[-b/2-m*height,height],[-b/2,0],[b/2,0],[b/2+m*height,height]])
    new=np.array(p['local_cross_section']['points_xz'],float)
    new[:,0]-=(new[0,0]+new[-1,0])/2
    return node,b,m,height,old,new

def draw_profile(ax,p):
    node,b,m,height,old,new=section_values(p)
    ax.plot(old[:,0],old[:,1],'--',color=colours['before'],lw=2,label='导入前（原模型底宽/边坡）')
    ax.plot(new[:,0],new[:,1],color=colours['after'],lw=2.3,label='导入后（JSON 断面）')
    ax.axhline(0,color='#cbd5e1',lw=.8)
    ax.set_title(f"{p['Name']}  |  {p['chainage']}\n节点 {node}；{p['remarks']}\n原模型：底宽 {b:.3f} m，边坡 {m:.3f}（水平/竖直）",fontsize=9)
    ax.set_xlabel('横向距离，渠底中心为 0（m）',fontsize=9)
    ax.set_ylabel('相对渠底高程（m）',fontsize=9)
    ax.set_aspect('equal',adjustable='box')
    ax.set_ylim(-.12,height+.2)
    ax.grid(alpha=.2)
    ax.legend(fontsize=8,loc='upper center')
    if p['lookup_status']=='ready_with_warning':
        ax.text(.98,.04,'源表尺寸冲突：按半径/边坡生成',ha='right',transform=ax.transAxes,color='#b45309',fontsize=8)

selected=['DYD_029','DYD_030','DYD_032','DYD_015','DYD_004','DYD_021']
fig,axes=plt.subplots(3,2,figsize=(14,12))
for ax,name in zip(axes.flat,selected):draw_profile(ax,features[name])
fig.suptitle('JSON 导入前后：同一模型节点的渠道横断面对比',fontsize=17,y=.99)
fig.text(.5,.015,'两条曲线按相同渠高绘制，渠底中心对齐；绝对渠底高程未改变。新曲线由表中尺寸推导，并非新增实测断面。',ha='center',fontsize=10)
fig.subplots_adjust(top=.92,bottom=.10,hspace=.75,wspace=.22)
fig.savefig(out/'section_profiles_before_after.png',dpi=170)
plt.close(fig)

fig,axes=plt.subplots(2,3,figsize=(16,9))
for column,name in enumerate(selected[:3]):
    p=features[name];draw_profile(axes[0,column],p)
    node,b,m,height,old,new=section_values(p)
    h=np.linspace(0,height,301);rows=np.array(p['hydraulic_lookup']['rows'])
    axes[1,column].plot(h,b*h+m*h*h,'--',color=colours['before'],lw=2,label='导入前')
    axes[1,column].plot(rows[:,0],rows[:,1],color=colours['after'],lw=2,label='导入后')
    axes[1,column].set_title('过水面积随水深的变化',fontsize=11)
    axes[1,column].set(xlabel='水深（m）',ylabel='过水面积（m²）')
    axes[1,column].grid(alpha=.2);axes[1,column].legend(fontsize=9)
fig.suptitle('三个 U 形渠段：断面形状及过水面积的前后变化',fontsize=17,y=.99)
fig.text(.5,.025,'灰色虚线：原模型梯形参数；橙色实线：JSON 圆底 U 形查表。渠底高程保持原值。',ha='center',fontsize=10)
fig.subplots_adjust(top=.91,bottom=.10,hspace=.55,wspace=.25)
fig.savefig(out/'u_sections_before_after.png',dpi=170)
plt.close(fig)

ready=[p for p in features.values() if p['lookup_status'].startswith('ready') and p['model_node_ids']]
with PdfPages(out/'all_applied_sections_before_after.pdf') as pdf:
    for start in range(0,len(ready),6):
        fig,axes=plt.subplots(3,2,figsize=(14,12))
        for ax,p in zip(axes.flat,ready[start:start+6]):draw_profile(ax,p)
        for ax in list(axes.flat)[len(ready[start:start+6]):]:ax.set_visible(False)
        fig.suptitle(f'已应用断面导入前后对比  {start+1}–{min(start+6,len(ready))}/{len(ready)}',fontsize=16)
        fig.text(.5,.02,'同一节点、相同渠高、渠底中心对齐；尺寸冲突断面在面板内标记。',ha='center',fontsize=10)
        fig.subplots_adjust(top=.91,bottom=.10,hspace=.75,wspace=.22)
        pdf.savefig(fig);plt.close(fig)
print(out/'section_profiles_before_after.png')
print(out/'u_sections_before_after.png')
print(out/'all_applied_sections_before_after.pdf')
