# 会话管理中的 length 处置

本功能位于独立实验项目 `pythonagent-scheduler`，版本 `0.2.0.dev3`。

## 识别范围

- Chat Completions：`finish_reason="length"`，包括只有推理而没有可见答案的情况。
- Responses：`status="incomplete"` 且 `incomplete_details.reason="max_output_tokens"`。
- 非长度原因的 incomplete 不进入本恢复路径；HTTP 上下文超限沿用现有强制压缩路径。

协议含义核对自 [Chat Completions 官方参考](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
及 [推理模型指南](https://developers.openai.com/api/docs/guides/reasoning)。第三方兼容服务是否正确报告这些字段，仍需实际服务测试。
`length` 不能单凭状态区分“输出额度不够”和“输入占满可用容量”；不能一律通过压缩解决。

## 恢复流程

1. 每个响应先保留原始审计。长度截断时，即使正文恰好是合法 JSON，也不执行其中的工具或最终决定。
2. 角色的有效历史、最新状态、证据交付标记和服务端父响应保持在最后一次已接受结果。
   原始截断正文仅用于审计，不作为已完成工作重放，也不拼接 JSON 后缀。
3. 在同一工作流、同一角色本地会话内保留原始待办指令与完整当前状态，通知模型上一份回答未被执行，
   要求重新给出符合当前契约的完整 JSON，压缩冗余解释但不遗漏必需字段或事实。
   不自动调高 `max_tokens`，不关闭思考，不更改科学参数。
4. 若已有可压缩的已接受历史，则用本次原生 `prompt_tokens`/`input_tokens` 补充容量判断。
   达到现有阈值时沿用独立模型压缩请求；最新状态与待办原样保留。没有输入计数时不编造计数，
   不默认探测 tokenizer 接口。新会话无已接受历史时，不尝试压缩不存在的历史。
5. Responses 重答引用最后一个已接受响应，不引用 incomplete 响应；若历史压缩，沿用现有上下文换链规则。
   每次完整工作流仍创建全新角色会话，不恢复上次运行。

## 配置与结束条件

四份配置均已加入：

```json
{
  "llm": {
    "length_recovery": {"enabled": true, "max_retries": 2}
  }
}
```

`max_retries=2` 表示最初请求之后最多两次长度恢复；不是工具查询预算。
设为 `0` 或关闭 enabled 时，首次截断即以专用错误结束，仍不会执行片段。
HTTP 上下文恢复不重置此计数；网络重试和原有 Responses 连续性检查各有原来的语义。

恢复位于一次 `RoleSessions.ask` 内，不增加角色评议或实验计数。
总角色轮次、科学实验次数、调度决定及技术执行重试上限仍生效。
持续截断最终输出 `stop="model_output_limit"`，附角色、已观察到的截断响应次数、供应商详情及 reason：

- `length_recovery_exhausted`：恢复次数耗尽。
- `length_recovery_disabled`：已关闭恢复。
- `protected_context_exhausted`：长度恢复前的历史压缩明确失败，无法继续容纳当前任务。

本处理不会把输出截断误报为科学否决，也不保证任意任务在既定输出额度下必能完成。
独立摘要请求自身的截断由现有摘要重试处理，未完成摘要不会替换有效上下文。

## 审计与用量

每次真实响应仍有原始请求/响应文件与角色调用记录。新增：

- `output_truncated`：截断响应、原生用量和 `applied=false`。
- `length_recovery_completed`：成功恢复及被丢弃响应标识。
- `length_recovery_failed`：未恢复的原因和截断次数。

成功结果的 `output_recovery.recorded_response_usage` 仅汇总该次恢复捕获的截断响应及最终响应；
它不是完整工作流费用。缺失用量保留 unknown（空用量及 null 汇总），不填成零；
只报告输入计数时允许用于容量判断，但不虚构输出计数。
统计完整费用应累计调用审计中的响应记录及 `context_summary`；恢复事件重复引用的用量不能再次相加。
底层 HTTP 或连续性重试发生的额外调用仍以各自调用审计为准。

## 验证

新增协议夹具覆盖 Chat/Responses、半截 JSON、可解析但标为 length 的工具调用、纯推理截断、
恢复耗尽、压缩联动、部分/缺失计数、父节点保持、HTTP 恢复不重置次数及工作流错误归类。
本轮未调用真实 GLM/Qwen/Gemma，未执行 VASP/Wannier。
完整结果见 [验证报告](../verification/length-recovery/REPORT.md)。
