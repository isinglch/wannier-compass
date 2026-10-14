> 中文阅读译本；执行主版本：[English](../RUNTIME.md)。
> 对应英文来源 SHA256：`4da88b1f0d1ed9270975880512948b187deaa8b1d3bc5067f9d60f6110b8ba28`。

# 紧凑的文件接口工作流

这是接口使用卡。科学判断读[首次试算卡](FIRST-TRIAL-CARD.md)，输入输出读[接口契约](INTERFACES.md)。CLI 与 MCP 调用相同科学代码；证据分析和选择不要求 SSH、Slurm、特定 AI 客户端或部署位置。

1. 提供经过核实的投影能带缓存。`prepare_evidence(cache, out_dir)` 一次生成 inspection、投影图和**尚未选择**的 `review.json`。没有缓存时先导入原始 DFT 数据。
2. 阅读本次证据与首次试算卡。用 `patch_draft(path, changes, expected_sha256)` 填入轨道、目标/参考/冻结带及物理解释。正文保存在文件中，每次只提交变化。
3. `review_requirements` 计算原门禁需要的精确坐标；坐标和自己的物理解释一起补入草案。工具不选择基组、不赋予态的物理角色、不扩大 frozen，也不豁免门禁。
4. `review_proposal` 一次生成原提案、选择图和当前导带问题。回答模板；选择改变后重新生成，身份必须对应。保存保守 `initial`，再用 `seal_stage` 封存 `real_first_try`。两者可以相同；initial 不执行。
5. 将封存的选择交给计算集成层：准备与核验接口矩阵，准备仅解纠缠输入，执行、核查真实产物/收敛，再做同 k 点对比。目前随包的完整执行后端是**可选 [Slurm 参考适配器](SLURM-EXAMPLE.md)**。外部后端需要单独的执行/导入记录集成，不能伪造记录。
6. 阅读数值摘要和图，区分观察、假设、下一步。重试前保存实际结果，并传入 `parent_result`。遵循[窗口重试卡](WINDOW-RETRIES.md)，区分科学尝试与技术重启。

执行集成可以自动完成边界之间的确定性工作；到科学选择、数据不足、证据拒绝或执行状态不明时停下。MCP 可以部署在数据旁边，CLI 可以由本地 shell、现有 agent 或用户选定的远程方式调用。

## 摘要与完整证据

`wannier-compass-run --workspace WORKSPACE TOOL.py -- ARGUMENTS` 默认返回不超过 8,000 字符的 JSON；完整 stdout/stderr、退出码、时间与工具哈希保存在 `log`，由 `log_sha256` 绑定。旧 CLI 仍可直接调用，`--full-output` 显式请求完整输出。

`ok` 仅代表命令正常退出。科学 `status`、接口/拟合准备状态、收敛、负面证据和数据门槛分别判断。运行层不赋予科学验收。逐 k 点数组留在磁盘；必要字段若列在 `details_in_log`，读取完整记录对应部分。`read_record_summary` 直接提取 JSON 指标，不要求模型重写评估；可用 RFC6901 `pointer` 提取确切字段，例如 `/numerical_checks/min_nouter`，命令日志则用 `/stdout/numerical_checks/min_nouter`。`details_required` 表示需先读取该证据再判断。

MCP 的工作区文件工具限制路径在 `WAN_WORKSPACE_ROOT` 内。包内 Markdown 只读：`read_text` 可直接读取 `workflow_guide` 返回的绝对文档路径，也接受对应的 `workflow/` 前缀，即使尚未配置工作区。这不授权读取工作区外的其他文件。CLI 的 `--workspace` 仅规定工作目录及日志/缓存位置，其他显式文件参数直接传给原 CLI，不构成沙箱。

## 草案与恢复

增量补丁按对象合并：普通字段替换、嵌套对象合并、数组整体替换。`null` 删除可选键，但 `task_scope_review.requested_scope_evidence` 例外：这是必需字段，主要流形任务明确保留它的 null 值。`expected_sha256` 使用模板、补丁或文本读取返回的原文件字节哈希；提案的 `review_sha256` 则绑定规范化的 review 内容。要求当前文件哈希，旧版本/补丁另存；不能修改封存阶段或 `records/`。模板中的 null 只是待判断项。

科学拒绝保持失败退出码，不自动重试。可选 SSH 只读查询可有限重试传输错误；修改性命令只试一次，响应丢失先核对是否完成。可选 Slurm 适配器先存提交意图，再提交；恢复已有作业时重新核验产物。队列消失不等于成功。

## 可选的评测记账

正常构造按上面的流程执行，无需调用费用工具，也无需提供 token 计数、API 日志或价格。用量记账是明确选择后才使用的独立评测工具，不是准备、执行、重试或验收条件。科学来源和计算日志仍属于正常流程。

明确要求成本评测时，提供模型客户端导出的原生 usage；程序不能从任意 AI 部署自行获知账单。`account_native_usage` 按原生请求/响应身份、模型、提供者和阶段记录，重复请求去重，缺失计数保留 null。支持 DeepSeek/OpenAI/Anthropic 原生字段或明确归一化的计数。已包含在输出中的 reasoning token 不重复相加。

`usage_report` 可按 provider/model/phase 筛选，并读取匹配模型的价格文件：币种、来源、生效日期、收费字段和每百万单价必须明确。缺失计数或费率时费用为 null；不用文本长度估 token，不写死价格。工具耗时与实际计算耗时分别记录，API 成本及科学效果需要匹配的模型测试。
