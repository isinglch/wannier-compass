# 事实工具与统一分页（0.2.0.dev10）

程序提供可复核事实，角色保留科学判断。没有新增工具次数预算，没有自动推荐窗口、轨道、最佳方案或科学接受阈值。

## 统计表与列式数据

`anchor_table` 默认 `layout="columns"`，可显式 `layout="rows"`。列式表中所有叶数组的第 i 项属于同一能带行。模型的 `band_slice` 已移除；底层原始表分页函数仅供程序内部使用。统计工具仍提供精确有界见证，不删减底层计算数据。

公共分页字段：`offset`、`requested_limit`、`returned_rows`、`total_rows`、`next_offset`、`next_query`、`byte_limited`。`limit` 的行含义由具体工具声明，例如 anchor_table 的带行、orbital_character 的领先通道分段；不是统一的 k 点数。大小限制可能让实际行数少于请求值，始终按 `next_query` 续读；它是完整的 `{tool, arguments}` 请求。`complete` 仅表示已经到最后一页，不证明模型读取了此前各页或完成了科学审查。

```json
{"tool":"anchor_table","arguments":{"offset":0,"limit":10}}
```

普通页在原工具内缩小后直接交付，不需要转存再进入另一套分页。无法放入单页的大对象仍完整保存，明确 `archive_scope` 与 `source_page`：档案只包含该次结果，不能把档案读完当作源查询完成。

`read_record` 先执行角色读取白名单和摘要裁剪，再处理 pointer；原始诊断 per_k/pairs 和投影 /states 不可读取。它默认保留行式 `value`，支持对均匀对象列表用 `layout="columns"`。超长 JSON 对象返回 `view="structure"`：每行 pointer 指向原文件中的字段，不指向 `/value` 包装器。字符串长度过大时 offset/limit 表示字符。始终保留原始文件哈希。

`repeated_identical_observation=true` 只说明同一角色在本次运行已收到完全相同请求与结果；调用仍真实执行，文件改变后正常返回新数据，不禁止重复核验。标记不随重复次数增长，不替代全局停滞恢复。

## 路径、EF 与通道变化

`overview`/`anchor_table` 的 `k_path` 公开来源校验后的路径段、标签、分数倒空间坐标与段间连接事实。复用已有 `path_geometry`，不另写一套 KPOINTS 解释器。缺失或失配时明确 unavailable，不推测连接。

`ef_crossings` 可传 `band_lo/band_hi/k_lo/k_hi`，offset/limit 按事件分页。输出两种证据：

- `sample_at_ef`：真实采样能量相对给定 SCF EF 恰好为零，不隐含容差。
- `sign_change_bracket`：同一路径段内相邻非零能量严格异号。`linear_estimate` 是线性插值的分数坐标，不是实际计算态，原始投影与领先通道只在端点 witnesses 给出。

不跨路径段检测，不把能量排序索引当作波函数分支跟踪，也不声称已覆盖完整费米面。没有近零采样并不证明采样间不存在接触/穿越。

`orbital_character` 保留完整指定 k 范围的通道均值、峰值与极值见证，增加 `row_kind="leading_channel_run"` 的分页 rows。offset/limit 仅分页分段，汇总始终覆盖完整指定范围；每段保存源 k 范围、路径段编号、能量范围、起点见证与领先通道集合。采用严格原始权重 argmax，并列全保留；全零集合为空。不按阈值合并近似相等权重，也不根据领先通道决定是否纳入轨道。

## 实验与残差

`residual_ranking` 增加可选 `k_lo/k_hi/band_lo/band_hi/energy_range_eV/region`。先过滤再排序；能量是 DFT 相对 SCF EF。region 为 all/frozen/outer/above_outer，窗口来自该 trial 的 approved-plan，而非现在的方案。`counts/metrics` 为全人口，`filtered_counts/filtered_metrics` 为查询子集，缺失状态计数和见证单列。

`trial_compare(left,right,population="baseline_target")` 读取当前 run 两个 trial 的批准方案、结果绑定和 comparison.npz，复用现有谱来源、能量、k 序列和执行状态校验，并用原生能量匹配重新计算。输出方案字段变化、收敛事实、覆盖和误差；仅目标人口与匹配 DFT 状态均相同时给 `metric_delta_right_minus_left`。不同人口不强行计算增减，不排序最佳方案。没有绑定原生谱的 command 适配器明确报工具能力/证据不足，不回退到不一致指标。

## 离线工具统计

```bash
python -m pythonagent.tool_audit /ABS/run_a /ABS/run_b --output /ABS/tool-audit.json
python -m pythonagent.tool_audit --runs-root /ABS/runs --output /ABS/tool-audit.json
```

只读 journal 中 kind=tool，按工具统计次数、超长转存率、同角色同请求重复率、耗时与返回字节。每个 run 独立去重，重复的 transport 事件不重复计数；重叠但参数不同的查询不计为完全相同请求。无模型调用、不打开 calls 或会话提示。统计不用于自动评判工具调用的科学必要性。

## 执行事实

五角色共享 `execution_facts(trial, view="summary")`，其他视图为 inputs/interface/constraints/commands。完整定义、读取边界和验证见 [信息入口与执行事实](STATISTICS-AND-EXECUTION-FACTS.md)。
