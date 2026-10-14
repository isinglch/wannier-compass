> 中文阅读译本；执行主版本： [English](../WANNIER-COMPARISON.md)。
> 对应英文来源 SHA256：`03e1b18ff4391fe8722cd3e481628c788f00556fda601d5fa3ccb084f72a36a7`。

# 每轮拟合对照

`tools/compare_wannier.py` 是正式调用入口，读取标准材料目录及一个独立试次：

```bash
python tools/compare_wannier.py submit --case-dir /work/material \
  --trial-id trial_001 --site sites/slurm.example.json \
  --postw90 /absolute/path/postw90.x --ntasks 36 --execute
```

`submit` 默认只返回脚本，`--execute` 实际提交 Slurm。脚本调用正式 `run` 入口，
该入口必须在对应 Slurm 计算节点 allocation 内运行；不在登录节点执行 postw90。
`--ntasks` 不得超过 allocation。以 site 中的环境初始化和 MPI launcher 启动。
可配套指定 `--run-metrics /path/run_metrics.py --ledger /path/ledger --experiment-id ID`
接入成本账本；无账本时仍在 validation_report.json 记录本次调用耗时和真实退出码。

程序读取 `03_wan_trial/trial_001/wannier` 的正式执行记录与必需产物，
在新的 `comparison/` 内以已执行 chk/eig 调用原生 postw90，逐个使用
`02_band/charge.npz` 和 `fit_path.json` 中的真实 DFT k 点，保留重复端点和断段。
所有能量均减同一份 SCF EF，不拟合零点平移。

固定输出：`fit_review.png/pdf/json`、`diagnostics.json`、`validation_report.json`，
以及同点插值数据和原生执行日志。完成后更新 trial 根目录的图和 Wannier 产物链接。
`wannier/` 的原生 `wannier90_band.dat/kpt` 仍单独保留；其各段采样密度由
Wannier90 原生逻辑确定，不能假设与 DFT 行数相同。

`diagnostics.json` 给出 实际frozen与outer区间的逐 k 点一对一最小能量差匹配，
包含 MAE/RMSE/P95/MAX 及未匹配数量。该诊断没有匹配误差截断，也没有波函数、
轨道或能带连接信息；不能将它当成完整流形或物理分支认证。
显示完整能区及选定窗口的叠图供人工/AI审阅，图与数值均不会自动赋予 fit_pass。
因此正常收敛但尚未经过外部审阅的图题写 `TOOL STATUS: NOT EVALUATED`；不能把
`fit_pass=null` 显示成 `NOT ACCEPTED`。只有未收敛等已知阻断才标为 not accepted。
到迭代上限但未收敛的源计算允许画诊断图，必须标为 PROVISIONAL，不能报告已收敛。
计算失败或必需产物缺失则不能进入本步骤。

不修改基组、窗口、num_iter=0 或其它科学输入；不重新执行解纠缠。
已存在 comparison 目录不会覆盖。新的解纠缠尝试使用新的 trial 编号。

同一科学试次若只因资源布局等技术原因在
`trial_NNN/technical/restart_NNN/` 重跑，可额外用 `--trial-dir` 指向该 restart。
该参数只接受 canonical trial 下的直接 `technical/restart_NNN` 子目录，比较结果仍留在
restart 内并标为 `technical_retry_private`；它不会覆盖原失败证据，也不会伪装成新的科学轮次。

同一路径也用于 `restart=plot` 技术输出恢复。`workflow.inspect` 会从恢复 manifest/record
识别 `technical_output_recovery=true`：比较入口要求恢复 WOUT 自己正常 `All done` 且六项
产物哈希完整，但 DIS 收敛、实际迭代数和慢收敛标记来自封存的来源 WOUT，而不要求恢复
WOUT 伪造或重复 DIS 行。来源达到迭代上限时，图题、diagnostics 和 validation context
继续标为 `PROVISIONAL`、`accepted=false`。若恢复依赖启动时已 no-op，恢复目录没有独立
产物集，比较入口会拒绝它；此时应直接使用已经完整认证的 canonical trial。

默认对照图是frozen上下各扩1 eV和dis上下各扩3 eV，来自实际执行win。已移除`priority_ef_pm1`及固定近EF绘图参数。outer含解纠缠背景，因此其全区能量匹配只作诊断，不能把所有背景带都当成必须拟合的目标。显示留白不充当验收域。
