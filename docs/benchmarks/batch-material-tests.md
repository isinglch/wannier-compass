# 批量材料测试：100种含磁性材料与200种SOC材料的论文对照

[返回项目主页](../../README.md#测试效果与成本对比) · [English](batch-material-tests.en.md)

本次测试考察WannierCompass在批量材料上的Wannier构建效果。Agent根据DFT投影能带选择轨道基组和能量窗口，运行Wannier90，并根据能带拟合结果决定是否继续调整。

两个测试集分别包含100种材料（含磁性体系）和文献公开的200种SOC材料。计算采用VASP及标准PAW-PBE赝势，未额外添加类氢投影轨道，以加权能带误差η₂ ≤ 20 meV作为通过标准。

## 测试结果

| 测试集 | 材料数 | 通过数 | 通过率 | 总尝试次数（含首次） | 平均每材料 |
|---|---:|---:|---:|---:|---:|
| 含磁性体系的100材料测试集 | 100 | 73 | 73.0% | 172 | 1.72 |
| 文献SOC200测试集 | 200 | 158 | 79.0% | 610 | 3.05 |

100材料测试集按约化元素组成和最终SCF磁空间群去重，保留不同磁空间群的材料。SOC200保留文献的200种结构，其中199种有有效评分，另1种计算中止，计入未通过。

本批测试首次构建采用`num_iter=0`，必要时再开启最局域化，最多100步。同一材料、同一基线模型下，20、50和100步的最局域化运行合并计为一次尝试；更换基线模型后另计。SOC200共461次非最局域化尝试和149次合并后的最局域化尝试；100材料测试集沿用原统计，共172次。

## 在同一SOC200材料集上与论文对照

我们采用[Jiang等人的公开材料结构](https://archive.materialscloud.org/records/fhp6p-vdm66)，独立完成VASP计算和Wannier构建。主要对照[论文](https://doi.org/10.1038/s41524-025-01835-8)中未添加额外类氢投影轨道的两组结果，比较最终通过率与构建尝试次数。论文采用Quantum ESPRESSO及两种赝势库，本测试采用VASP标准PAW-PBE赝势。

| 方法 | 赝势库 | 总尝试次数（含首次） | 平均每材料 | 通过数 | 通过率 |
|---|---|---:|---:|---:|---:|
| 论文方法，未补类氢轨道 | PseudoDojo | 1,699 | 8.495 | 152/200 | 76.0% |
| 论文方法，未补类氢轨道 | modified-pslibrary | 882 | 4.410 | 181/200 | 90.5% |
| **WannierCompass，未补类氢轨道** | **VASP PAW-PBE** | **610** | **3.050** | **158/200** | **79.0%** |

WannierCompass以平均每材料3.05次尝试取得79.0%的通过率。相比论文中未补类氢轨道的两组，构建尝试数分别减少64.1%和30.8%；通过率高于PseudoDojo组，低于modified-pslibrary组。本测试采用按需开启、最多100步的最局域化设置，论文公开计算的最局域化上限为4000步。这里用构建尝试次数表示尝试投入。

论文进一步添加类氢投影轨道后，PseudoDojo和modified-pslibrary两组通过率分别提高到98.5%和99.0%，总候选次数分别降至323和281次。这也为后续改进投影基组提供了参考。

评估采用同k点能带对照。金属评估到EF + 2 eV，绝缘体评估到采样CBM + 2 eV，权重展宽σ = 0.1 eV；本测试以固定目标能带的Hungarian匹配计算η₂。论文表中的总次数为公开记录的额外参数优化次数加200次初始运行。

## 能带误差分布

### 100材料测试集

![100材料测试集的η₂分布](../../benchmarks/batch-material-tests/nano100/plots/eta2_histogram_0_30meV.png)

### SOC200测试集

![SOC200测试集的η₂分布](../../benchmarks/batch-material-tests/soc200/plots/eta2_histogram_0_30meV.png)

两张图展示0–30 meV区间，均值和中位数使用全部有效评分。完整数值见逐材料CSV。

## Wannier轨道spread分布

每个材料分别统计所有Wannier轨道spread的平均值与最大值，单位为Å²。

### 100材料测试集

![100材料测试集的spread分布](../../benchmarks/batch-material-tests/nano100/plots/spread_histograms.png)

### 200材料测试集

![SOC200测试集的spread分布](../../benchmarks/batch-material-tests/soc200/plots/spread_histograms.png)

通过材料的平均WF spread，按每个材料等权平均，分别为2.228 Å²和2.524 Å²；上方分布图则包含全部有效模型。

## 能带拟合示例

以下展示两个通过案例和两个未通过案例。η₂为统一复核后的结果，图中灰线为DFT能带、红色虚线为Wannier能带。

| 测试集 | 材料 | η₂（meV） | 结果 |
|---|---|---:|---|
| 100材料 | Cr₂O₃ | 0.090 | 通过 |
| 100材料 | CsEuCl₃ | 36.060 | 未通过 |
| SOC200 | AlCo | 13.119 | 通过 |
| SOC200 | NaI（原始化学式INa） | 136.995 | 未通过 |

<details>
<summary>展开能带拟合图</summary>

**Cr₂O₃**

![Cr2O3能带拟合](../../benchmarks/batch-material-tests/examples/nano100-Cr2O3.png)

**CsEuCl₃**

![CsEuCl3能带拟合](../../benchmarks/batch-material-tests/examples/nano100-CsEuCl3.png)

**AlCo**

![AlCo能带拟合](../../benchmarks/batch-material-tests/examples/soc200-AlCo.png)

**NaI**

![NaI能带拟合](../../benchmarks/batch-material-tests/examples/soc200-NaI.png)

</details>

## 数据下载

| 数据集 | 逐材料结果 | 汇总 | 完整展示包 |
|---|---|---|---|
| 100材料 | [results.csv](../../benchmarks/batch-material-tests/nano100/results.csv) | [comparison.csv](../../benchmarks/batch-material-tests/nano100/comparison.csv) · [summary.json](../../benchmarks/batch-material-tests/nano100/summary.json) | [nano100.zip](../../benchmarks/batch-material-tests/nano100.zip) |
| SOC200 | [results.csv](../../benchmarks/batch-material-tests/soc200/results.csv) | [comparison.csv](../../benchmarks/batch-material-tests/soc200/comparison.csv) · [summary.json](../../benchmarks/batch-material-tests/soc200/summary.json) | [soc200.zip](../../benchmarks/batch-material-tests/soc200.zip) |

SOC200的逐材料次数拆分见[attempt_counts.csv](../../benchmarks/batch-material-tests/soc200/attempt_counts.csv)，计数说明见[attempt_accounting.json](../../benchmarks/batch-material-tests/soc200/attempt_accounting.json)。两个数据集的CSV均列出保留模型的窗口与spread；窗口单位为eV，以原始SCF费米能为零点。

论文四组统计见[paper_comparison.csv](../../benchmarks/batch-material-tests/paper_comparison.csv)，本页汇总见[summary.json](../../benchmarks/batch-material-tests/summary.json)。

## 讨论与启示

Jiang等人的这项综合工作富有启发性：其结果表明，常规赝势提供的原子轨道投影集合，在部分体系中可能不足以充分覆盖目标能带。补充类氢投影轨道因而不只是提高自动化成功率的技巧，在一些情况下也可能是获得高质量Wannier模型的必要条件。

当前的WannierCompass能够有效减少构建尝试、优化自动化流程，但轨道和窗口选择仍受可用投影基组的限制。本次SOC200测试中，仍有约20%的材料未通过当前评价，也表明仅靠这些选择尚不足以解决所有材料的Wannier构建问题。这篇重要的工作告诉我们，我们应当考虑到，补全投影空间对最终提高构建鲁棒性的关键作用。

## 来源

- Y. Jiang et al., *Robust Wannierization including magnetization and spin-orbit coupling via projectability disentanglement*, npj Computational Materials **11**, 353 (2025). [论文](https://doi.org/10.1038/s41524-025-01835-8)
- [Materials Cloud公开数据集，版本2](https://archive.materialscloud.org/records/fhp6p-vdm66)：SOC200结构来源。
- [论文参数候选记录的历史公开快照](https://archive.materialscloud.org/records/tc8pz-ddy75)：四组候选次数与论文第6页的额外优化次数相符。
