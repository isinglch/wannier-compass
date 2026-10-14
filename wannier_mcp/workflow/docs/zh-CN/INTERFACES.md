> 中文阅读译本；执行主版本：[English](../INTERFACES.md)。
> 对应英文来源 SHA256：`7f35079305669f2962d2de5c77192f810b9ffdd4b8fd2416fe486f2f926f8f66`。

# 科学接口契约

主 API 使用显式文件和记录，不规定 AI 客户端、SSH、调度器、账号、队列、集群布局或部署位置。当前科学引擎为 VASP/Wannier90，并未实现任意引擎互通。

| 阶段 | 输入 | 输出与边界 |
|---|---|---|
| 导入能带 / `collect_band` | prepared case、核实的 SCF/band 文件及官方执行记录 | 缓存、路径与 DFT/投影图；不新增 DFT |
| 原始文件转换 / `vasp_to_cache.py` | PROCAR/OUTCAR、采样/自旋及所需几何 | 带来源身份的缓存；转换不等于执行认证 |
| 证据 / `prepare_evidence` | 本次 band cache、新输出目录 | inspection、投影图、未选择草案及日志；等待判断 |
| 增量编辑 / `patch_draft` | 未封存草案、变化、当前哈希 | 新草案和旧版本历史；不封存 |
| 门禁坐标 / `review_requirements` | 对应缓存、inspection、草案 | 精确坐标与待填物理解释；不替 AI 作物理选择 |
| 提案 / `review_proposal` | 同一组输入、新目录 | 选择/分析/检查 JSON、选择图、导带问题；仍可能待审阅或缺数据 |
| 封存 / `seal_stage` | 审阅草案、context；首次/重试还需 parent、trial ID、reason，重试需实际 parent result | initial 或 selected stage 的不可变记录；initial 不执行 |
| 接口准备 / `prepare_interface` | SCF/band、缓存、封存选择；可选核实的共享波函数/显式网格 | 依赖基组的输入和 manifest；不启动引擎 |
| 接口认证 / `validate_interface` | 真实矩阵、本征值和对应记录 | 维数、实际网格/窗口/排除/数据检查；路径可行不等于网格可行 |
| Wannier 准备 / `prepare_wannier` | 官方认证接口及封存阶段；窗口复用还需新缓存/分析/决策 | 新输入/manifest，`num_iter=0` 与必要输出；不启动引擎 |
| 求解检查 / `inspect_wannier` | 输入/执行记录及实际产物 | 产物、收敛与 provisional 状态；不验收拟合 |
| 同点比较 / geninterp、plot | 精确路径、DFT/Wannier 本征值、声明目标映射 | 数值记录和图；不认证物理/对称性 |
| 实际结果 / `record_result` | 真实绑定产物及外部 assessment | 后续重试使用的不可变结果身份；不自动入知识库 |
| 数值摘要 | 存储 JSON | 紧凑证据；不编造验收 |
| 可选评测用量 | 明确要求的成本评测、客户端导出的原生 usage、可选价格 | 去重用量和可追溯费用；普通构造不调用这些工具，也不要求账单记录 |

精确 CLI 参数、数据形状与合法字段由原工具 `--help`、policy/schema 和 [MANIFOLD-SELECTION](MANIFOLD-SELECTION.md) 规定。采样、能量参考、数据身份和阶段祖先分别保留；新输出目录不覆盖封存结果。英文表中有完整 CLI/MCP 对应关系。

## 执行集成

执行是单独的后端问题。集成层接收引擎输入与 manifest/阶段身份，产出真实文件、退出码/耗时证据和绑定输入输出的记录。科学校验器再检查产物。调度器“成功”不能替代认证或收敛。

**当前能力**：官方 Wannier 执行与完整对比保留已有 Slurm allocation 要求。DFT run-stage/run-interface 另有 local 路径，submit 只支持 Slurm。新的 `slurm_runner.py` 是这些命令的可选编排；证据、选择、准备与文件绘图无需它。SSH 只是可选传输。

**外部后端边界**：只有矩阵或自己写的 run.json 不会自动成为官方接口。已有门禁要求认证记录。未来 PBS/直接执行/容器/远程服务后端需实现并验证执行与导入记录契约；不能伪造 Slurm ID、跳过门禁或改历史记录来宣称兼容。

## 返回值

紧凑返回包括 `ok`、`exit_code`、`status`、`result`、`log`、`log_sha256` 与预算；逐态数组和正文在完整日志。`ok=true` 也可能只是成功生成“待审阅”提案。科学状态另判。失败保留非零/null 退出码、原因和 `automatic_retry=false`；运行层不作物理验收。
