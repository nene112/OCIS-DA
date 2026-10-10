# 指定 DLL 与横断面查表

当前 DLL 从用户指定源码仓库的 build_manual_no_waga/OcisMILPNet_dll/Release 编译并复制到 tools。交互周期水力状态、时钟、闸门和缓存恢复修复保留。哈希见 native_manual_no_waga_manifest.json。

## 生成与导入

```powershell
python tools/prepare_dayudu_cross_section_tables.py
python tools/import_dayudu_cross_sections.py
```

输入仍是 input/CrossSection_dayudu_summary.geojson；原始 CSV 字段全部保留。输出 input/CrossSection_dayudu_hydraulic_tables.geojson 以及同名 .report.json。

新版格式 metadata.format=ocis_section_tables_v1；每段包含 model_canal、桩号区间、model_node_ids、局部 points_xz 和 hydraulic_lookup。查表列为 h,A,T,P,I，单位分别为 m,m²,m,m,m³。模型节点按 mesh/stake.json 的总干渠桩号映射精确关联，不推导 GIS 坐标或绝对底高程。原模型 zb 保持不变。

查表通过 client.set_CrossSection(path) 导入；client.get_cross_section_report() 返回成功、匹配节点、未解决断面及超过表内最大水深的节点数。get_stepdata_sim 支持 node_section_id、node_A、node_B、node_P、node_HR、node_pressure_integral。

局部断面覆盖规则参数。面积、顶宽、湿周、摩阻水力半径进入 sediment 和 Roe_new 的全渠与单渠段求解。储水项使用真实 A(h)-A(h_old)，波速用 sqrt(g*A/T)。现有水面梯度形式的压力项保持不变，用查表 A 参与计算；不额外叠加压力积分梯度，以免破坏静水平衡。I 保存为一致的静水压力积分，供诊断及后续守恒通量实现使用。

断面表中顶宽按水深分段线性插值；A 与 I 对顶宽插值积分，保证 dA/dh=T，I 的水深导数为 A。A→h 使用单调反查。表顶以上明确采用竖直延墙外推，不代表漫溢或有压管流模型。

## 数据解释与限制

U 形断面 D3.4/D3.2 解释为直径，17°按侧壁与竖直方向的夹角；采用与圆底相切的侧壁。原表 h+0.6 与口宽关系可核对：口宽对应加高前的渠高。弧底梯形按所给半径及边坡建立相切圆弧。

36 个断面可导入，包含全部三个 U 形断面；2 个弧底断面 DYD_016、DYD_020 的口宽与半径、边坡、渠高存在冲突，状态为 ready_with_warning，当前由明确给定的半径和边坡确定形状，须核实原表。9 个未解决断面明确保留原模型：缺边坡的 DYD_003，缺尺寸的渐变段 DYD_034，以及 5 个隧洞、2 个涵管。有压闭合结构不能用敞口断面外推替代；不猜测 DN00*2 的缺失直径。

## 自动接入重建

案例 OCIS_dataConfig.json 的 SIM 配置新增：

```json
"cross_section_path": "input/CrossSection_dayudu_hydraulic_tables.geojson"
```

_load_client 在网格、初始水深及底高程设置完成后导入断面。相对路径以案例目录解析，隔离运行会随案例复制。dayudu 当前配置已启用。未解决断面会提示部分导入，旧 DLL 缺少报告接口时拒绝查表文件。

## 验证

明渠缺参数补全：DYD_003从相邻上游同类圆底梯形DYD_002借用边坡1:1；DYD_034以相邻上游DYD_033的矩形断面近似，渠高2.1m、口宽2.5m、边坡0。生成器保留原始CSV字段和transition类型，以parameter_imputation记录供体及采用值，两处标记ready_with_warning。当前可导入38个断面，7个封闭结构继续保留原模型几何。以后的生成及仿真输入都会采用此补全；此前仿真报告仍对应补全前版本，未自动重算。

```powershell
python test_native_cycle_checkpoint.py --cross-section data/dayudu/input/CrossSection_dayudu_hydraulic_tables.geojson
python tools/test_dayudu_cross_section_lookup.py
```

原生源码仓库另有 cross_section_lookup_test 和 cross_section_solver_test 两个 EXCLUDE_FROM_ALL 测试目标，分别覆盖几何/反查及实际求解器的封闭周期静水、体积守恒。

真实 U 渠段具有自由出流边界，与封闭静水测试分开。真实渠段检查查表属性、有限推进、回滚重现及非法导入不改变状态。结果保存在 output/cross_section_lookup。重建水位验收标准未改变，查表测试通过不等于整段边界重建已满足水位指标。
