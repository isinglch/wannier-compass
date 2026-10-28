# 调度器实验扩展

当前运行增加第五个操作调度角色，具体职责、事件和恢复契约见 [SCHEDULER.md](SCHEDULER.md)。
下方保留原四个科学角色和执行底层的架构说明。

# 决策归属与模块边界

## 科学决定的唯一入口

`roles.py` 定义四个职责；`guidance.py` 从本项目 `guides/mcp/` 加载 MCP 项目的科学原文。两份项目 skill 与 26 篇英文文档完整保存，`mcp-sources.json` 记录来源、哈希和章节。必要内容按角色直接交付，补充章节通过已有读工具访问。原文摘录记录准确行范围，不重新摘要科学内容。首版手写科学摘要已退出提示词。各角色另有新职责卡，`SOURCE-ADAPTATION.md` 负责解释原文中的旧协议如何映射到新架构，指导中的科学判断不转为程序自动否决。

`contracts.py` 定义完整方案和各角色 action 的唯一运行时 Schema，`specs.py` 为 Schema 增加工具说明。`schemas/` 是供人查阅的导出文件，运行时不读取另一份 Schema。更改契约时从 `contracts.py` 重新导出，不需要与父项目同步。

planner 通过 `write_plan` 保存原始方案；程序只新增独立 metadata 和 validation，不填充或改写科学字段。无效方案也保存，所有格式/事实错误一并返回。有效不表示科学通过；只有 reviewer 的 approve 才允许执行。

reviewer 只能批准或提出问题，不能代改方案。strategist 给出开放文本策略，不使用写死的重试方向枚举。evaluator 看到失败、不收敛、覆盖缺失和误差；程序不把这些事实自动等同于科学失败。

## 数据与客观检查

| 模块 | 责任 |
| --- | --- |
| `cache.py`, `channels.py`, `formats.py` | 真实软件文件、轨道列、自旋维度、WS 平移傅里叶插值与明确标注的能量匹配 |
| `plotting.py`, `plot_paths.py` | 读取确切数据绘制 PNG/PDF，绑定来源和方案，保存同 k 点谱与图像索引；不生成科学方案或验收结论 |
| `evidence.py`, `statistics_only.py` | 来源可追踪的统计和有界见证；限制角色读取整批原始数组，底层保留完整计算 |
| `execution_facts.py` | 统一记录原始设置、生成输入、观测维度、窗口计数、命令状态及来源，供五角色只读查询 |
| `validation.py` | Schema、来源哈希、声明数值、索引、basis 维度、窗口次序和实际输入的数学约束 |
| `plans.py` | planner 专有写权限、不可覆盖版本、准确版本审批与来源绑定 |
| `execution.py` | 操作者命令、独立试验目录、真实输出读取与实施一致性；不推导窗口 |
| `workflow.py` | 角色路由、交接与资源上限；科学路线由 action 决定 |
| `guidance.py`, `guides/mcp/` | MCP 科学经验原文、项目 skills、按角色/阶段交付与来源验证；无在线同步或科学选参 |
| `llm.py`, `sessions.py`, `compaction.py` | 传输、每次启动的新会话、独立模型压缩；无材料规则 |
| `state.py`, `journal.py` | 原子文件、锁、哈希和审计 |
| `config.py`, `cli.py` | 操作者基础配置和独立命令行 |

事实错误检查能核对结构化 evidence_refs 中的数值与文件声明；不声称能用程序判定所有自然语言推理。真实软件的输入维度不一致、文件不完整或内容被修改属于实施错误。投影强弱、是否应纳入某轨道、窗口是否科学合理属于角色判断。

工具不向模型开放任意写文件或 shell。`read_record`/`read_text` 只能访问当前 case、当前 run 与本项目 guides；禁止跨根目录与私有旧会话。shell 执行权属于操作者配置的执行器，四个角色均不能新增命令。

## 原生执行器

1. 比较缓存声明的 band 源文件哈希，以及 SCF/band 的结构和势文件；核对 SCF EF。
2. 复制固定源文件到新试验目录。把批准的相对窗口加 SCF EF 转为软件绝对能量；这是坐标转换，不是重新挑选窗口。
3. 写入明确的 solver、basis、NUM_WANN 与操作者配置的网格。保留来源的物理设置，固定电荷密度接口只覆盖所需运行标签。
4. 运行 VASP，核对实际 EIGENVAL 网格、维度、窗口态数、win 参数、eig/AMN/MMN 完整性与顺序。
5. 运行 Wannier90，检查正常结束、输入未改变及 HR 维度，读取配套 wsvec 平移进行插值。原生流程明确启用 `use_ws_distance` 并要求平移文件存在。
6. 保存完整诊断、固定目标基线和 comparison.npz，自动绘制同 k 点对比与残差图；向 evaluator 返回状态、收敛事实、指标和文件路径。没有自动科学通过标准。

`input_binding.json`、`result_binding.json` 绑定输入、源文件及实际输出。HR 文本只有六位小数，重建本身存在舍入误差；历史回放依据对应文本精度核对原生 postw90，见验证报告。

实际输入违反公开数学约束时，问题回到 planner；任何补救仍需要新方案和 reviewer 新批准。命令失败交给 evaluator 判断后续策略。资源预算耗尽单独报告，不伪造模型结论。

## 注册其他执行软件

配置 `execution.adapter=command`，例如：

```json
{
  "adapter": "command",
  "commands": [["/ABS/python", "/ABS/adapter.py", "{plan_file}", "{result_file}", "{trial_dir}", "{case_dir}"]],
  "timeout_seconds": 3600
}
```

只有四个路径占位符进行字面替换，使用 `shell=False`。注册程序须读取批准的方案并产生 JSON：`{"status":"completed","observations":["实际观察"],"metrics":{}}`。禁止在适配器内填入材料经验式科学默认值。通用 command 后端核对批准文件完整性及结果协议，但无法代替第三方程序核对软件实际输入；需要相应的 read-back 检查。原生适配器有专用完整性核验。

如果以后要让模型主动补算，应增加显式的数据操作契约及实施工具，科学参数仍由角色提供；不得恢复按顶端/尾态阈值自动触发的路线。

## 绘图与主循环的边界

PlotManager 在角色首次调用前生成 DFT 图，PlanStore 在有效方案写入后生成方案图，Workflow 在执行完成后读取绑定的 comparison.npz 生成对比图。绘图不改动 plan 文档与审批记录。异常通过 visualization 和 plotting_warnings 公开，不触发科学改参或科学拒绝。Matplotlib 延迟加载并使用 Agg，绘图耗时单独记入 journal。

新适配器若提供同 k 点数据，可调用 plotting.save_comparison 生成协议文件：明确原始证据版本、准确方案 digest、kcoords、两套相对同一 SCF EF 的能量、原目标和实际执行/收敛状态。绘图读回时重新核对来源、维度、k 点顺序和能量参考，不自动平移曲线或重新分配 k 点。

## 固定格式与提示词适配

操作者固定的解纠缠格式要求是 `solver.num_iter` 的 integer/const=0；执行入口与实际 win 读回都检查显式零值。其余 DIS 参数仍来自方案。SOURCE-ADAPTATION.md 位于原文引用之后，逐项映射旧科学要求到当前 reason/evidence_refs、角色权限和结果定义。原文不会注册旧工具或覆盖当前 Schema。

Evidence.projection_audit 扫描当前区间的全部保留采样态，计算原始选中权重、最强未选通道、分位数和见证。验证事实自动包含 frozen 摘要，工具另存摘要及有界见证并绑定来源/方案，不向角色开放完整 /states 表。弱指标阈值显式指定，不自动科学否决。实际接口的态数检查不混入路径投影。

results.py 定义当前指标和 DIS 状态解析。收敛/迭代上限须有连续迭代行及明确标记；不需 DIS 由维度和无冲突日志确认；未知/矛盾不产出可信比较。complete/stop 必须说明停止依据与未完成工作；数据阻塞与目标完成分别报告，数值接受仍为 null。

## 事实分析与分页

`observations.py` 负责无损列式编码、实际返回行数和游标；普通原始页在同一工具内按大小减页，`next_query` 保留原范围。`read_record` 对超长对象给出原文件的 JSON pointer 结构，不再归档读取包装器。相同角色/请求/结果的再次出现只标注事实，不禁用工具或消耗新增次数预算。

`band_analysis.py` 复用绘图路径校验器；能量变号只在明确的路径段内检查，精确零采样和线性估计分开。领先通道分段保留并列与全零，不跨段拼接。`trial_analysis.py` 提供显式过滤与同来源谱的指标差；目标人口或实际匹配状态不同不提供总体误差差值。工具不判定纳轨道、选窗口或科学验收。

`tool_audit.py` 仅离线读取 journal 的工具事件，统计频次、超长转存和同角色重复率；不读模型提示或推理，不调用模型。门内停滞调度与流内服务错误恢复仍属独立的运行控制问题，本次工具接口改造不更改这些控制策略。

## 观察台结果图片

`viewer_plots.py` 只读 trial 的现有结果与绘图manifest，提供固定PNG/PDF文件白名单、原文件校验和结构化的轮次图片索引。`viewer.py` 的同源loopback接口服务这些原始图像字节；不重新绘图、执行计算、读任意文件或修改运行日志。前端按轮次展示图片与原有指标，通过稳定DOM和图片固有宽高保持阅读位置。角色提示、工具、科学决策和验收逻辑不受此展示层改动影响。

## 统计入口与统一执行事实（0.2.0.dev10）

角色目录不注册 band_slice。read_record/read_text 通过 statistics_only 的真实路径白名单，诊断 JSON 在应用 pointer 前先裁成摘要视图；原始谱、投影、矩阵与私有历史不作为角色文件读取入口。原始数据仍供程序计算、验证和绘图。精确见证与全区间统计均保留，不生成科学建议。

每次执行由 Workflow 创建 ExecutionFacts，Runner/NativeExecutor 按实际阶段记录。result、失败交接和 scheduler.last_execution 携带同一摘要；execution_facts(trial,view) 检查 run/trial/输入/方案和来源绑定。原始 NBANDS、生成请求、输出维度分列；未知阶段不填猜测值。失败坐标以无损区间编码保留。记录层不改变 NBANDS 生成规则、工具预算、角色轮次或科学验收。

## 操作者配置与进程启动（0.2.0.dev11）

agent.json 是唯一公共运行配置，models 只覆盖公共 llm 差异。settings.py 使用标准库解析配置及路径；startup.py 在科学库和 HTTP 客户端导入前加载环境模块、可选密钥文件、显式环境变量和路径，并按配置选择 Python。运行时环境不进入 Workflow 的科学配置。./agent、python -m pythonagent 与安装后的控制台命令共用启动入口，cli.py 负责命令路由；旧平面配置仍支持。

环境只作用于当前进程和其子进程，源 case、科学规则、角色工具权限和会话隔离不变。密钥文件不执行 shell，不作变量替换；module 名称按字面参数传递。check 是本地依赖/路径检查，不进行网络认证或科学执行。
