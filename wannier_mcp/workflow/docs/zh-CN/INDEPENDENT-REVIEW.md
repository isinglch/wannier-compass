> 中文阅读译本；执行主版本： [English](../INDEPENDENT-REVIEW.md)。

本版基组判定采用 v3：先声明物理任务锚点与集中的深谱块，再决定主 p/d/s 及额外通道。额外通道需有任务相关证据；不能先扩充深成键目标再用“完整性”要求加轨道。每个通道记录纳入与省略风险，无法确定时可显式探索。深背景无全局隙也可不加入基组，留在 dis 并独立提高 frozen 下沿。广泛深成键模型需要明确用户请求。字段和实际门槛以相邻英文规范的 v3 节为准。
> 对应英文来源 SHA256：`8b38441bdaa061a744479aae7e4b0cec5498fd9591c1a48002f7d499a99972db`。

# 独立审阅：证据、判断、复核

先按简明的[首次试算选择卡](FIRST-TRIAL-CARD.md)作科学选择；[详细决策顺序](WORKFLOW-DECISIONS.md)用于复杂谱块解释。本文只规定证据包、复核和盲测边界。

这个文档是通用审阅协议，不含任何材料的历史基组、窗口或拟合答案。运行方先固定任务定义、DFT 输入、工具版本和预算，再交给新的审阅上下文。只在文件系统也已隔离、历史结果不可访问时称为盲测；仅清空聊天上下文并不足够。已用于修改规则的开发材料只能用于回归，不是外推能力的独立证明。

## 输入包

使用现有投影 band-path NPZ；不要求事先计算投影 DOS 或独立 NSCF mesh。`review_packet.py`自动复算 `inspection.json`，产出全部逐带权重 CSV、标准 DFT/投影能带 PNG+PDF 以及哈希清单。输出只含当前 DFT 证据，无轨道/窗口建议或历史 Wannier 拟合。必须看完整的路径图及逐带数据，不能只读一个候选列表。

```bash
python3 tools/review_packet.py --cache /input/band_charge.npz --out-dir /new/review_packet
```

出图统一使用 `tools/plot_dft_bands.py`；每次 `manifold_selection.py propose` 的 `selection.png/pdf` 是 initial、real_first_try、retry 的标准窗口图，并写图片身份清单；已封存选择用其 `render` 命令重画。正式阶段默认更新[跨材料选择图集](SELECTION-GALLERY.md)；纯选择测试按标准阶段目录命名时也默认更新，交付前检查图集中的条目与警告，必要时显式运行图集工具刷新。Wannier 后用 `tools/compare_wannier.py` 和 `tools/plot_fit_review.py` 输出同 k 点的拟合对照。详见[绘图接口](PLOTTING.md)、[DFT 图](DFT-PLOTS.md)、[拟合对照](WANNIER-COMPARISON.md)。临时 SVG 不替代正式图。

## 必须作出的物理判断

1. 先说模型覆盖什么：所选价层及相关低导带，还是一个明确的更大范围。用元素价层作起点，但逐个说明每个入选壳层在哪些实际带组承担独立目标角色。对于单独、很深而已填满的强峰，先判断它是否属于本次目标价层；它可能成为扩展模型，而不是默认强迫加入。每个未选壳层在**同一个 `(k,n)` 态**与选中通道比较；低球内权重也可能代表间隙态，不能被“没有明显其他通道”自动视为目标态。
2. 对占据价带，先列目标权重的最低态与非目标/弱投影背景。将 `dis_min` 置于目标完整包络下沿以下；背景少量进入 outer 可供解纠缠。独立审阅 `frozen_min`，上移到不再强迫下方背景态进入内窗；目标与背景若能量交叠，就只冻结干净内部并记录其余目标态。最后判断是否另行排除有占据、非目标归属和全局隙的深能级连续前缀；没有合格前缀也继续试算。分别报告全局隙与逐 k 直接隙，它们不决定窗口必须共用下界。只有干净保护隙经明确审阅后才可把 `dis_min=frozen_min` 放在隙中。
3. 对导带，先审阅目标投影在哪些带/能量扩展和何处拖尾，再确认 outer 既涵盖目标又有足够候选态；70% 及尾部只是诊断，不能代替带顶截断和 `N_outer >= num_wann`。`propose` 的 `upper_reference_audit` 和导带问题还要检查**outer 以上的全部已算带（参考域内外均包含）**：参考域内的高覆盖率不能证明它本身已经包住目标壳层；若仍有已选投影分支穿过 outer 上界，应重新审阅参考域和上窗，若只是弥散尾巴也不强迫全部纳入。先确定基组并依据干净目标分支独立选 frozen，**之后**运行“frozen 逐态反证筛查”：逐k检查已选 frozen 态的绝对目标权重及竞争通道。低端反例复核下界，高端反例复核上界；不能拿来增删基组、改变目标/exclude/dis，也不能用“零反例”扩大窗口。弱投影可能来自未投影的间隙成分，要看能带而不是套固定 veto。以后每个新判据都要先登记其[生效位置和允许用途](MANIFOLD-SELECTION.md)。
4. `review.json` 对每个元素×壳层必须写可核查的带号或能量区、原始权重特征、同态竞争和纳入/不纳入理由。`target`、`reference`、`frozen` 三套带组可以不同，不能为通过计数机械复制。`reference_upper_status` 必须和文字一致；参考达到最高计算带时应如实标为右截断。窗口建议要说明为什么保护目标、为何不强迫非目标、空带是否足够。不要把一段通用套话重复粘到每个轨道。

## 选择与审计

草稿 `propose` 后必须读取 `conduction_review.py questions`，它明确问出参考域外的最强已选投影样本和当前 frozen 之上的带边/首个正面候选。首次实际试算和retry须逐项回答，再 `propose`/`check`；`decision_stages.py select` 会拒绝无回答、窗外反例或无探索声明的模糊暂缓。此门槛仅保证问题被审阅、坐标可复算，不能证明AI的物理角色判断正确。

按[选择契约](MANIFOLD-SELECTION.md)写 `review.json`，再运行 `manifold_selection.py propose`。随即运行下列只读诊断，它检验当前已选 frozen、遗漏通道同态竞争、重复理由、状态自相矛盾与路径数值门槛。报告中的 0.10 是**绝对投影的负面提示线**，不是删轨道或自动缩窗口的物理定律；表面上没有警告更不构成扩窗或拟合验收依据。

```bash
python3 tools/review_diagnostics.py \
  --cache /input/band_charge.npz \
  --inspection /proposal/inspection.json --review /proposal/review.json \
  --selection /proposal/selection.json --out-dir /new/diagnostics
```

在封存 initial 前审阅报告和图；选择 real_first_try 前重新审阅，如果改了 review 就重新 propose 和诊断。initial 是保守留底，real_first_try 才是要提交的首次方案。保留两套图、输入/工具身份、理由及诊断，不得事后覆盖。数值门槛失败不可提交；物理不确定但数值可执行时，可按文档明确使用 exploratory trial 并保留不确定性。实际 Wannier 接口 mesh 检查发生在**接口矩阵生成之后**，绝不可把 band-path 计数称为实际 mesh 通过。

## 测试记录

每次记录 actor、模型/提示版本、知识检索状态、输入 SHA256、基组/排带/两窗口、选择图、诊断、提交和实际计算时间、拟合图与收敛状态。明确要求成本评测时，另记录外部模型原生 token usage；其他情况省略模型账单。所要求的 provider usage 无法取得时写 `null`，不能按文本长度估算。技术重启和科学改选分开计数。只有 `num_iter=0` 的解纠缠结果、完整产物和预先声明精度区的实际拟合都核查后，才能报告相应层级的成功。
