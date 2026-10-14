> 中文阅读译本；执行主版本： [English](../REFERENCE-WORKFLOW-V0.2.md)。
> 对应英文来源 SHA256：`92f6b8bc5ce5e0ba18281b22c3c666886708e2ccb45fa177f252758fa50cad27`。

# Wannier 参考工作流

本文件只串接执行阶段。**轨道、目标流形、exclude、dis 和 frozen 的科学判断，以 [WORKFLOW-DECISIONS.md](WORKFLOW-DECISIONS.md) 为唯一顺序与优先级。** [PHYSICAL-REASONING.md](PHYSICAL-REASONING.md) 解释物理边界；[MANIFOLD-SELECTION.md](MANIFOLD-SELECTION.md) 给出选择命令与 JSON 字段。其他执行文档不另立选轨道/选窗标准。

| 阶段 | 输入与动作 | 留下的证据 |
|---|---|---|
| DFT 素材 | [准备 SCF 和投影 band](DFT-PREPARATION.md)，或登记匹配的既有结果；[导入缓存](VASP-CACHE.md) 并画完整 DFT/投影能带 | `01_scf`、`02_band`、`charge.npz`、路径几何、图、来源哈希 |
| 选择 | `review_packet.py` → `inspect` → 审阅基组/带组 → `propose` → `review_diagnostics.py`；按主决策文档迭代 | 正面 frozen 候选、独立负面反证、`selection.json` 和 `selection.png/pdf` |
| 阶段封存 | [封存 initial，再明确 select real first try](DECISION-STAGES.md)；后续 retry 引用真实结果；[跨材料选择图集](SELECTION-GALLERY.md)默认更新 | 两阶段各自的理由、图、候选、采用值、诊断与输入身份；同实验图集 |
| Wannier 接口 | [默认单次均匀网格接口 NSCF，或封存WAVECAR后的`ALGO=None`接口](WANNIER-INTERFACE.md) 生成 AMN/MMN/EIG；用**该接口自身**的本征值检查 exclude、每 k 态数、计算带顶和矩阵一致性，共享模式还核对与封存本征值一致 | 接口 `.win`、矩阵、实际网格门槛；默认无单独 mesh/PDOS 前置，重复测试可预先准备一次共享波函数 |
| 解纠缠 | [运行 Wannier90](WANNIER-EXECUTION.md)，显式 `num_iter=0`，独立控制 `dis_num_iter`；写出 `hr/rmn/tb/band` | 收敛状态、实际迭代、时间、`chk/hr.dat/r.dat/tb.dat/band.dat/band.kpt` |
| 核验 | [在原 DFT k 点比较](WANNIER-COMPARISON.md)，展示 frozen±1 eV 和 dis±3 eV 视图；按预先声明的完整目标和精度要求评价 | 拟合图与数字、未匹配态、问题定位；图区留白不是选窗或验收区域 |

初次结果不理想时，先判断是基组与流形身份错误、价带背景被误排/误冻、导带目标被截断，还是数值解纠缠空间不足，再按对应阶段修订。窗口试探、接口技术重跑和新增科学试次分别记账。跑通流程不等于模型通过。盲测包不带已知材料答案；对开发材料的回放是回归，不是未见材料泛化。
