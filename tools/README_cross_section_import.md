# 指定 DLL 与断面读取支持

`tools/OcisMILPNet.dll`及配套依赖来自用户指定的 `build_manual_no_waga/OcisMILPNet_dll/Release` 目录。该版本已移植交互周期快照修复，重新编译并复制到当前项目。来源与哈希见 `native_manual_no_waga_manifest.json`。切换前 DLL 与依赖保存在 `native_backups/before_manual_no_waga`，本版本修复前 DLL 保存在 `native_backups/manual_no_waga_before_checkpoint.dll`。

Python 包装器支持：

```python
client.set_CrossSection(json_file_or_directory, cs_start=0, cs_end=44)
```

调用前须先初始化水动力网格，并执行端点和闸门类型校正。索引从 0 开始且包含两端；省略 cs_end 时读取至文件末尾。坐标必须是三个有限数值。原汇总 JSON 的 geometry 为 null，会明确报错，不将零条导入当作成功。

单文件通过临时独立目录导入，避免扫描其他案例的 GeoJSON；目录方式会写出 ReWriteMesh。路径使用 Windows 本地编码传递。

复现测试：

```powershell
python tools/import_dayudu_cross_sections.py
```

兼容副本与报告位于 `data/dayudu/output/cross_section_import_manual_no_waga`。副本保留原断面表所有记录，缺测 XYZ 使用现有 mesh/input.txt 和 neighborId.txt，沿总干渠 next-node 路径累计平面距离，就近关联每段起点桩号。模型路径长 31575.078 m，表覆盖 30750 m，关联仅为近似模型坐标，不能作为实测。每条 geometry_provenance 记录所用节点及距离偏差。原 JSON 保持不变。

DLL 日志确认读取 45 条记录，匹配 298 个节点，其中 297 个节点高程改变。逐节点验证水深保持不变、wh=zb+h，底宽与边坡不变。接口当前仅平滑并更新床面高程，不读取横断面形状数组、底宽、边坡或圆弧参数。此次验证副本没有配置为后续重建的实测断面数据。

此版本的 save_states/set_states 已恢复完整节点变量、内部时钟、包装器步号、闸门、开度映射、池段容积与结果缓存。分水流量诊断也使用正确分支记录，避免读取零流量虚拟节点。源码补丁见 native_patches/manual_no_waga_cycle_checkpoint.patch。

分别在原网格及显式导入兼容 JSON 后运行8周期 A/B/A 回归，全部通过。带 JSON 的验证逐节点确认导入后的床面高程在所有试算、恢复及8次最终推进后保持不变，每周期最终只推进3600秒。简要报告位于 data/dayudu/output/checkpoint_regression_manual_no_waga_json/verification_summary.json。

已有边界重建脚本尚未自动调用此接口。带 JSON 的验证仿真显式调用 set_CrossSection，不能将此测试等同于历史重建结果已经使用该 JSON。此次接口仍未应用原表中的横断面形状数据。
