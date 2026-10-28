# 统计入口与执行事实（0.2.0.dev10）

此版本将独立实验副本合入 pythonagent-scheduler。程序产出可复核事实，角色作科学判断；没有新增科学阈值、窗口建议、基组选择或工具次数预算。

## 角色的数据入口

- band_slice 不再注册或可调用。统计工具保留 anchor_table、ef_crossings、orbital_character、window_diagnostics、projection_audit、residual_ranking 和 trial_compare，以及有界精确见证。
- read_text 读取科学 Markdown、case 设置及 trial 输入/执行日志；不读取原始 EIGENVAL/PROCAR/OUTCAR、Wannier 谱与矩阵、charge/comparison 数组。
- read_record 读取指导 JSON、方案/审批、trial 摘要、交接和统计 observation。diagnostics 仅暴露 counts/matched_error_metrics/method；先裁剪摘要再处理 pointer，不能通过 /per_k/pairs 或 /states 恢复原始整表。
- 真实路径校验防止软链接别名绕过；list_records 和文件哈希不授予读取权限。
- 原始数据仍留在磁盘，完整计算扫描、校验、能量匹配和绘图不受限制。统计覆盖不代表科学认可；默认不对弱投影加隐含阈值。

## 每轮执行事实

Workflow 为每个新 trial 创建 execution-facts.json，Runner 与 NativeExecutor 更新实际阶段。记录包括源缓存维度、原始设置、生成输入与覆盖来源、实际观测维度、矩阵验证状态、完整窗口计数、命令状态和错误。未启动、未观测、未验证保持显式状态，不以源配置推定执行结果。

例如 Cu 实测：原始 INCAR 的 NBANDS=20，生成输入为 32，实际 EIGENVAL 为 32 带、512 个 k 点。三者独立记录；没有恢复 NBANDS 必须等于源 cache 的否决。DIS 是否需要仍按实际 retained nb 与 num_wann 判断。

result、失败交接和 scheduler.last_execution 携带统一摘要。五角色共享只读 execution_facts(trial,view)，view 为 summary/inputs/interface/constraints/commands，核对 run/trial、输入版本、批准方案、源文件哈希及已发布结果绑定。失败坐标以无损连续区间编码，保留全部受影响点，不输出全能量表。

## 已知限制与验证

实验快照在 2026-10-16 完成 Cu/Qwen no-think 全流程，run=20261016T122007Z-c3b0db218523：1 次实验、2 版方案、VASP/Wannier90 正常退出、DIS 155/200 收敛；整体 MAE 0.148095 eV、最大误差 4.127552 eV。模型自行完成，数值验收为 null，不能据此认定科学精度达标。

本轮确认执行事实进入 evaluator 实际请求。未触发真实模型失败修复分支；该分支只有离线测试和归档回放覆盖。重复查询、结构化输出修复和自然语言推理矛盾仍存在，本次合入不宣称已修复。

开发验证使用正式 tests/ 完整 discovery，不再排除实验中的两个原始工具成功测试：改为内部分页覆盖，角色拒绝行为由专门边界测试验证。测试使用本地模拟 HTTP、临时文件和短 Python 子进程，不调用真实模型或 VASP/Wannier90。

local-tests/、runs/、本地模型配置、密钥和 Python 缓存由 .gitignore 排除；正式 tests/ 源码随 Git 提交。历史实测和实验副本继续留在本机，不复制为项目源码。

正式合入验证：完整 unittest discovery 169 项通过、0 项排除；Ruff 检查通过。基线套件为 152 项。
