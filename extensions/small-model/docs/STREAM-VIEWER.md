# 流式响应与只读观察台

从 `0.2.0.dev5` 起，四份模型配置和示例配置默认 `llm.stream=true`。未指定此项的旧配置
仍按非流式工作，使用 `llm.stream=false` 可显式关闭。不更改角色提示词、会话、思考开关、
科学方案、工具协议或输出额度。

## 启动

```bash
pythonagent-scheduler view --runs-root /ABS/RUNS --port 8765
```

同一服务器浏览器访问 `http://127.0.0.1:8765`。远程机器在本地终端运行：

```bash
ssh -N -L 8765:127.0.0.1:8765 USER@SERVER
```

然后在本地浏览器打开同一地址。替换 USER/SERVER 为实际 SSH 连接目标；若本地端口占用，
可换端口映射。网页只绑定 loopback，不提供公网访问、写入、运行/停止工作流或任意文件读取接口。
没有额外 Python/Web 服务依赖，不加载外部 CDN。

运行计算仍使用原命令，并让 --runs-root 指向观察台监控的同一目录。网页可先启动等待新工作流，
自动列出新运行。选择运行后通过浏览器 SSE 接收事件，断线重连按事件字节游标续读。
记录更新不会自动滚动到底部；刷新时尝试恢复本标签页的工作流、角色筛选和阅读位置。
手动滚动会取消尚未完成的位置恢复。需要查看末尾时点击“到最新记录”，不会开启持续跟随。
关闭网页或观察台不影响计算。网页和历史输出均不用于模型上下文，不恢复旧运行会话。

## 显示内容

按 planner、reviewer、evaluator、strategist、scheduler 筛选。实时展示可见答案文本、实际 JSON
决定及理由、角色交接、工具名及关键返回状态、协议拒绝、上下文压缩、length 恢复和外部软件事件。
JSON 是当前角色协议，网页不会额外调用模型把它编造成角色聊天。

从 0.2.0.dev6 起，工具卡片显示实际返回条目数、总数、分页游标与文本预览；展开“完整工具返回”
按需读取主审计中该次工具的原始 result，显示完整 JSON，不只显示错误/方案状态字段。
原始返回本来只是文件索引时，展开仍忠实显示索引，不把尚未返回给模型的文件冒充工具结果。
旧展示日志缺少数据时只读关联原工具审计补齐，不改写旧日志；关联存在歧义时明确标为不可用。
这项展示不改变模型的工具输入、结果去重、上下文或科学决策。

生成中的文本明确标为尚未提交；完整输出与“角色决定通过协议校验”分开显示。
角色批准、软件成功、科学目标满足仍以工作流记录为准。不会展示模型请求/system 提示词、
Authorization/config 密钥或供应商独立 reasoning 字段。原始请求及推理审计仍在 calls 中，网页不提供读取接口。

顶部 token 只累计角色调用与摘要调用中供应商实际报告的 total_tokens（包括完成接收的 length 回答），
不重复加 transport/恢复事件。未报告或断流时无法确认的消耗不填零，所以这是“已报告 token”，
不能当成完整费用。旧运行没有 events.jsonl 时，从原 journal 与 exchange 的 response 字段只读回放，
不能补出以前未保存的逐字流。

## 协议处理

Chat Completions 用 delta.content 汇集文本，保留 finish_reason；要求完整的 [DONE] 终止。
Responses 使用 output_text.delta 展示，但最终以 completed/incomplete/failed 事件内 response 为准。
两种协议均复用原有 RoleSessions、压缩和 length 处理；片段不写入有效角色历史，不执行工具，
不与断线重试或 length 重答拼接。

连接在终止前断开时丢弃该次业务输出，按原传输重试次数重发同一请求，网页将每次尝试分开标记。
length 仍由会话层请求完整重答。无终止标识、错误事件、非法 JSON/SSE 或原生 tool_calls 不会作为
已完成答案；本项目继续使用原 JSON 文本工具契约。流的读取也受请求时间预算约束，心跳不能无限延长。

供应商需返回 text/event-stream；不支持 SSE 时明确报错，操作者可设置 stream=false。
Chat 默认请求 stream_options.include_usage=true。若兼容服务不支持此选项，可设置
llm.stream_include_usage=false；缺失用量保持未知，不默认调用 tokenizer。

events.jsonl 为展示用的追加日志，文本按约 0.1 秒/1024 字符条件批量写入；仅有新的可见文本时才产生文本事件。
journal.jsonl 及完整响应审计仍是主要记录。calls/<call-id>.stream.jsonl 保留原始 SSE 数据事件，
合并结果的 _stream_audit 给出该路径与事件数。展示写入失败不改变科学运行。

流式让用户更早看到首批输出，并不保证模型完成整次推理或计算更快。

协议参考：[Chat 流式事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、
[Responses 流式事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)。

## 每轮结果图片（0.2.0.dev9）

选择工作流后，“每轮计算结果”按 trial 展示能带拟合图、残差图、执行/解纠缠状态、方案版本与目标误差。各轮可独立收起，图片可点击查看原图或PDF。“计算返回”事件提供对应轮次的跳转入口。历史运行从现有 result.json 和 plots/manifest.json 加载，不重新计算或重写日志。

图片按原始尺寸比例预留空间并延迟加载；未变化的轮次不在轮询时重建。刷新恢复阅读位置会等待图片列表和消息历史加载完成，普通更新不主动滚动，用户交互会取消待恢复位置。角色过滤只过滤消息，不隐藏每轮结果。

新增同源只读 `/api/trials?run=ID` 与 `/api/trial-plot?run=ID&trial=trial_001&plot=fit_review&format=png`。图片仅限对应 trial/plots 的 fit_review、residuals PNG/PDF，经manifest哈希/字节数和方案绑定检查后返回，不开放任意文件或HTML读取；路径逃逸和目录/文件软链接被拒绝。图片生成不代表科学通过，显示指标沿用已有目标人口，不重新判定科学结论。
