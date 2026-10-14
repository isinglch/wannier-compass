> 中文阅读译本；执行主版本：[English](../SLURM-EXAMPLE.md)。
> 对应英文来源 SHA256：`64c0bda35c9a36da55125c68763b27d19b67b7bfc2f6832602875d37196cbbe6`。

# 可选 Slurm 参考流程

这是执行适配示例，不是主科学接口。它假定已有获准使用的 Slurm 环境及真实程序路径。MCP 可以直接部署在那里，无需 SSH；其他环境使用[文件接口](INTERFACES.md)。随包的完整运行适配器目前只支持 Slurm。

首个集成测试用用户提供的简单、非保密单质结构即可。仓库不包含结构、赝势、结果或轨道/窗口答案。通过[DFT 准备工具](DFT-PREPARATION.md)生成输入，或提供已核实数据；实际材料放仓库外。

复制 `workflow/sites/slurm.example.json` 或 shared-WAVECAR 版本到私有工作区，填写真实执行路径、环境、资源并运行 preflight。账号、分区、启动器和限制仅属于该私有 profile；模板不能原样提交。

`CASE` 是已有 prepared case，`SITE` 是核实的 profile，`ROOT` 是工作区，`POSTW90` 是真实程序。完整命令见英文版本。先 `slurm_runner.py configure/start --phase evidence --execute`：后台控制器不调用模型，生成证据后停在 `scientific_decision_required`。已有缓存可直接使用 `prepare_evidence`。适时读取 stage_status 或 `slurm_runner_status`，避免模型反复查队列。

封存 initial/real first try 后，`configure/start --phase trial --trial-id trial_001 --postw90 ... --execute` 执行接口、Wannier 和对比，停在 `fit_review_required`。可显式提供 shared-source、mp-grid、窗口复用的 interface-source 和 ntasks；原门禁仍生效。控制器不会决定科学验收或重试参数。

MCP 对应 `configure_slurm_runner`、`advance_slurm_runner(background=true, execute=true)`、`slurm_runner_status`，另需 `WAN_ALLOW_SUBMIT=1`。CLI 明确 `--execute`。

配置绑定工具/profile 哈希。已有作业续跑不重复提交；丢失成功响应可从对应完整日志恢复。提交状态不明时核对原 submission JSON/脚本，通过 adopt-job 绑定确切作业。队列消失但无官方执行记录不等于成功。recover-lock 只允许在原 host 回收已死进程的锁。

科学拒绝需审阅；部分技术执行保留记录，必要时走原 technical-restart 流程，不覆盖产物或混淆尝试数。`remote_command.py` 只是可选 SSH：声明为只读的查询可有限重试传输失败；修改命令仅试一次，响应不明先核实。agent/MCP 已部署在计算环境时无需它。
