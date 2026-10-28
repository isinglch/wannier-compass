# 实际接口带数

`0.2.0.dev4` 取消原生执行器中“接口 EIGENVAL 总带数必须等于源 cache 总带数”的核验。
接口 k 点数量与集合仍必须匹配配置；输入请求的 NBANDS 仍来自源 cache，允许软件实际输出不同带数。

## 统一维度来源

验证实际 EIGENVAL 后：

```python
nb = energies.shape[1] - len(plan["excluded_bands_1based"])
```

这里 `nb` 是应用原已批准排除列表后的实际保留带数。`.win` 的 num_bands、EIG 行数及
编号、AMN/MMN 维度都与它核对。执行器从验证返回事实读取该值，传给
`disentanglement_state(wout, dis_num_iter, nb, num_wann)`；不再传 cache 带数。

这样，cache 总带数等于 num_wann、但实际接口更多时，不会因为 cache 的相等关系误判
无需 DIS 或把真实 DIS 标为维度矛盾。反过来，实际保留带数等于 num_wann 且日志符合
原判读规则时，可以正确标为 not_required。缺失必要 DIS 证据仍是 unknown。

## 可见信息与保留检查

`actual-input-validation.json` 及执行结果的 `actual_input_facts` 记录（包括 DIS 未确认的结果）：

- `source_num_bands`：源 cache 总带数。
- `interface_num_bands`：实际接口 EIGENVAL 总带数。
- `retained_num_bands`：排除后实际 `nb`。
- `num_wann`：原批准方案的 Wannier 数量。

这些是实际执行事实，不触发程序自行修改科学方案。后端能力说明向角色公开上述规则。
源 cache、目标带范围、投影、窗口、排除列表以及固定基线比较均不变；新增接口带不会
静默加入原批准目标。实际网格上的窗口态数约束仍按全部实际保留态计算。

k 点数量/集合、Win/EIGENVAL 的 k 顺序、EIG 能量与编号、矩阵完整性、来源绑定、
批准方案的窗口/求解参数和正常软件退出检查均保留。实际 `.win` 与矩阵维度不一致、
矩阵截断及 VASP 段错误仍不能通过；本变化不修复 VASP 内部崩溃。

## 验证

新增真实文本格式的小型接口夹具，覆盖接口增带/减带、排除后计数、k 点不匹配、
错误 win/矩阵维度和截断矩阵。执行器链路测试保留真实验证、DIS 解析及 HR 插值，
仅替代外部软件启动、输入准备和文件绑定，覆盖两种 cache/实际相等关系及缺失 DIS 证据。
本轮未启动真实 VASP/Wannier，也未调用模型。

见 [验证报告](../verification/actual-nbands/REPORT.md)。
