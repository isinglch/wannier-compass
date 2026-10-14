> 中文阅读译本；执行主版本： [English](../DECISION-STAGES.md)。
> 对应英文来源 SHA256：`d012898a76280b435251d78a7facfaca58afaec656c4369e81b6e4d6195ee23e`。

# 方案阶段、试算与经验接口

轨道及窗口选择遵循 [唯一决策顺序](WORKFLOW-DECISIONS.md)。本文只定义阶段封存和重试记录。

工具给出可复算的证据与必要约束，AI结合本次投影和通用物理思路提出方案。`initial` 是不使用案例经验的保守基线；`real_first_try` 是提交前综合审阅后明确选定的方案；后续 `retry` 依据真实结果修订。两阶段可以完全相同，不要求为了展示AI作用而改参数。

这里的 initial 仍需AI/人审阅基组与谱块，**不是完全确定性的自动选轨道算法**。保存 actor、模型、提示版本和输入，才能重放与比较。数值通过不证明科学正确；阶段封存也不证明上下文隔离。

| 阶段 | 保存位置 | 是否执行 | 计数 |
|---|---|---|---|
| initial | `03_wan_trial/initial/` | 不直接执行 | 不占 trial 编号 |
| real_first_try | `trial_001/records/selection/` | 明确 select 后可准备；实际网格仍需检查 | 科学方案 1 |
| retry | `trial_002/records/selection/` 等 | 关联上个方案及其结果后选择 | 科学方案递增 |
| technical restart | 原 trial 的 `technical/restart_NNN/` | 原方案身份不变、输入另行准备 | 不增加科学方案编号；实际运行另计 |

每个新选择阶段均保存 `inspection/review/analysis/decision/selection/validation/context/retrieval.json`、`review_diagnostics.json/md`、`conduction_review.json`、`selection.png/pdf` 和 `STAGE.json`。initial 的导带文件保存待审问题；real_first_try/retry 保存逐项核对的答案。STAGE含输入与文件SHA256、代码身份、父方案、具体差异和理由、工具耗时；旧阶段仍按其原文件清单只读校验。封存后拒绝原位覆盖；后续计算依赖同一份方案身份。阶段页面为 `03_wan_trial/index.html`；同一实验的[跨材料图集](SELECTION-GALLERY.md)默认写入实验根目录 `selection_gallery/index.html`，生成失败会在命令返回中给 `gallery_warning`，不改变已封存阶段的状态。

## 使用顺序

先完成[目录与DFT素材](MATERIAL-LAYOUT.md)，按[首次试算选择卡](FIRST-TRIAL-CARD.md)使用当前band缓存运行 `review_packet.py`、撰写review并反复propose。草稿还不是已选定的执行方案。首次试算或retry封存前，必须用 `conduction_review.py questions` 逐项处理参考域外的已选投影与当前 frozen 上方的带边/正面候选；将答案写入review，重新propose，再运行 `conduction_review.py check`。`decision_stages.py select` 会重算这项门槛。

独立审阅先生成[证据包与复核报告](INDEPENDENT-REVIEW.md)：`review_packet.py`保存正式DFT/投影能带和逐带CSV，propose后`review_diagnostics.py`检查**已采用**frozen中的弱目标态及重复/矛盾理由。名为“frozen 逐态反证筛查”的判据只作用于先前独立提出的 frozen 区间：低端反例提示上移下界，高端反例可否决较高候选或提示下调上界；不能从“通过”推导扩窗，更不能增删基组或改变 exclude/outer。证据包保存在case的独立review目录并在context中记录哈希/路径；阶段封存时工具重新计算并哈希绑定本阶段的诊断报告。`record_consistent`只证明记录/数值一致，不能证明AI给出的轨道或谱块理由正确。

准备context，例如：

```json
{
  "experiment_id": "experiment-identifier",
  "task": "当前选定轨道完整流形及明确精度目标",
  "evaluation_mode": "blind",
  "actor": {"kind": "ai", "id": "selector", "model": "实际模型标识", "prompt_id": "提示版本或哈希"},
  "guidance": [{"origin": "general_rule", "description": "本次工具包的通用物理判断规范"}],
  "external_ai_cost": null
}
```

`guidance.origin`可为task、human、general_rule、case_experience、previous_result。initial与blind禁止case_experience；previous_result只用于retry并关联其父方案的真实结果。外部历史结果应声明为case_experience，不能改名为本轮结果或通用规则。实际外部访问隔离仍由评测运行器负责。

```bash
python tools/decision_stages.py initial --case-dir CASE \
  --review initial-review.json --context initial-context.json

# AI查看 initial 的指标和图，可保留或调整review。检索当前默认关闭。
python tools/decision_stages.py select --case-dir CASE \
  --parent CASE/03_wan_trial/initial --trial-id trial_001 \
  --review first-review.json --context first-context.json \
  --reason '根据本次投影保留或修改各项选择的具体理由'
```

select自动创建新trial，但不预建interface/wannier/comparison。`decision_stage.role=initial`或缺少封存记录的旧decision，不能准备/运行新的接口或Wannier作业。旧结果仍可只读检查；复用旧矩阵时须显式提供当前选定的cache/analysis/decision，再通过既有矩阵复用检查。

接口命令的analysis和decision均指向 `trial_001/records/selection/`。数值上仍按原流程执行：band判断 → 接口矩阵生成 → 实际接口网格约束 → `num_iter=0`解纠缠 → 同DFT点比较。接口按明确配置使用单次 NSCF，或已封存 WAVECAR 的 ALGO=None 路线。没有增加独立mesh/PDOS。四个输出开关及10000步上限保持原约定。

选择方案必须通过原有数值门槛；未通过的initial保留图和问题。不得为完成阶段转换硬改通过状态。用独立草稿重新审阅，ready后再select。initial一旦封存便不修改；输入DFT或任务变了则建立新case。

`needs_window_review`不是终止指令。先读具体原因：若**当前已采用 frozen 内**有逐态负面反例，必须修改内窗并重提案；`trial_intent` 不能豁免这个负面门槛。若是 outer 尾部或目标归属不确定、而当前内窗和数值门槛已合格，AI应在既有预算内使用[探索试算](MANIFOLD-SELECTION.md#不确定时继续探索试算)：带明确理由的`trial_intent.mode=exploratory`及`window_override`，保留高能遗漏警告，选择并执行real_first_try。数值空间不足时转入补空带；预算或输入限制明确禁止补算时才报告具体阻塞。initial不加载探索声明；retry依据真实结果改窗，不能将“未确定最优方案”当成放下材料的理由。

## 实际结果、下一轮与技术重跑

下一轮科学动作按[窗口重试卡](WINDOW-RETRIES.md)执行。低导带尚未保护时先审阅干净目标态；高导带已保护较多时可固定基组和 dis，试着降低 frozen 上沿。后一种释放约束不要求将合法目标态认作非目标，也不靠逐态反证决定方向。本文定义这些动作的登记与封存。

正式运行器保存时间、输出身份及真实状态。比较图完成后编写assessment，至少区分 `observations`（观察）、`hypotheses`（解释假设）、`next_action`；可额外记录人工验收、任务指标、阈值与证据、外部AI用量。执行失败也可记录，不能编造拟合结果。

```bash
python tools/decision_stages.py record-result --case-dir CASE \
  --trial-id trial_001 --assessment assessment.json
python tools/decision_stages.py select --case-dir CASE \
  --parent CASE/03_wan_trial/trial_001/records/selection \
  --parent-result CASE/03_wan_trial/trial_001/records/results/result_001.json \
  --trial-id trial_002 --review next-review.json --context next-context.json \
  --reason '依据上一轮具体问题修改窗口或基组'
```

结果快照核对运行与方案身份，保存日志/输出引用和哈希、观测/假设、时间；`knowledge_status=candidate_not_promoted`，不自动认证拟合或写入知识库。结果可追加记录（例如先记录失败，再补图或人工评估），不得覆盖。计算文件保持原位。

技术故障恢复使用：

```bash
python tools/decision_stages.py technical-retry --case-dir CASE \
  --trial-id trial_001 --reason '具体技术故障与修复，科学方案不变'
```

返回全新interface/wannier/comparison路径；使用同一selection调用正式prepare/run，不能覆盖原计算。节点故障、启动修复属于技术重跑；改基组/窗口属于新科学试次。修改解纠缠预算/容差必须明确记录，不能隐藏成等价技术重跑。目录预约不表示执行；结果导出按不同run记录路径计真实执行，不按工具调用数或结果快照数计次。特殊重跑的比较可使用geninterp_path/plot_fit_review，输出放返回的comparison目录，再record-result收集。

## 检索接口与消融

```bash
python tools/decision_stages.py retrieve --mode disabled --query query.json --out retrieval.json
python tools/decision_stages.py export-case --case-dir CASE --out trajectory.json
```

当前retrieve返回有身份的空receipt。`experience_assisted --snapshot ID`仅返回`backend_not_configured`，不查询网络或向量库，不伪造命中。未来adapter应先完成材料/结构族隔离及快照过滤，再返回可追溯来源、适用条件和证据；当前校验器故意拒绝非空伪造命中，接入真实backend时需同步扩展实现与测试。

推荐消融臂：保守baseline执行、AI审阅且关闭检索、AI审阅并允许检索。baseline臂也显式select同一initial并加`--baseline-control`，不会直接提交initial。第三臂须等真实检索backend实现；当前不能据空接口报告检索收益。各臂固定任务/DFT、工具版本、验收范围、预算，分别用全新case/上下文；当前命令不自动创建消融实验或隔离模型上下文。

export-case输出完整阶段记录与已登记结果、去重的真实执行引用，便于后续索引或训练数据整理；大产物仍为哈希引用，不嵌入向量。支持记录经验的输入与输出，不等于工具已经自动学习。通用发布包不包含任何材料的案例答案。

## 可选评测的用量口径

正常构造要求科学与执行记录，不要求模型 token 或账单。仅在明确做成本评测时使用下述外部模型记账项；缺省不记录，不阻塞科学阶段或结果快照。

- 方案阶段工具耗时、接口执行wall_seconds、Wannier实际wall_seconds分别保存；累计运行耗时按唯一run去重。排队时间依赖Slurm日志，不能混称Wannier计算时间。
- `external_ai_cost`来自外部模型运行器的provider usage/日志；可保存input/output/cache tokens、单位、请求ID及计量来源。无法取得时为null，不填0，不以文字长度估算真实token。
- 试次、技术重跑、模型请求三种计数独立。新增结果快照不会凭空增加一次计算；失败和重跑成本都保留。


当前 v3 阶段说明：initial 可以保留探索性 trial_intent 作为不可执行的假设元数据；它仍不能准备接口或运行求解器。真正计算必须显式选择 real_first_try 并通过所有原有执行门槛。
