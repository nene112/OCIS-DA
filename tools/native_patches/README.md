# 交互周期状态快照修复

当前 `tools/OcisMILPNet.dll` 已包含此补丁；旧 DLL 保存在 `tools/OcisMILPNet.before_cycle_checkpoint.dll`。

源码所在项目：`D:/Algorithm dev/0_MultiProj_OCISscip_proj`。
修改 `OcisMILPNet_dll/src/OcisMILPDll_export.{h,cpp}`，补丁见 `cycle_checkpoint.patch`。
源码的其他既有改动未包含在本补丁中。

`save_states` 保存完整节点值、内部时间步、包装器步号、闸门参数、开度映射、池段容积历史和结果缓存；保存操作不再修改 DetaH 或 GCoe。
`set_states` 恢复这些值。初始化和 reset 清除快照后，未保存时恢复为无操作；网格尺寸不匹配时拒绝恢复。
单对象只有一个内存快照；filename 是兼容参数，不会生成持久化文件。网格初始化、修改时间配置或 reset 后应重新保存。
已有 Python 试算函数无需更改调用顺序：周期开始保存，每次尝试前恢复，选择结束恢复，最后以选定控制量推进一次。
DA_sim 预热仍在恢复水力状态后显式清零时钟，保持原有预热行为。

编译：

```powershell
cmake --build build --config Release --target OcisMILPNetDll
```

验证：在 OCIS-DA 根目录执行 `python test_native_cycle_checkpoint.py`，依赖本地 dayudu 数据。
8 个连续周期，每周期 A/B/A 和最终 A 都从同一快照推进；验证结果一致、控制量 B 有效、数值有限、时钟恢复和最终仅推进 3600 秒。
报告：`data/dayudu/output/checkpoint_regression/result.json`。
DLL 构建通过，存在项目原有编译警告。验证覆盖单渠段 Roe 求解器；不宣称覆盖所有泥沙或并行线程模式。
试算产生的磁盘输出文件不属于内存快照，使用试算时应关闭文件输出。

分水诊断修复：`gates_Q` 优先读取求解器 sim_action 最新记录，避免虚拟节点 Q=0 误触发开度。该记录位于交互周期起点，须与上一个已执行周期设定值对齐。6 小时独立回放验证了此滞后关系；8 周期 A/B/A 回归重新通过。
