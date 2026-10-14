> 中文阅读译本；执行主版本： [English](../PLOTTING.md)。
> 对应英文来源 SHA256：`5eac310792084e81e301513aa3c6f79cbf78914036df0ee12dab73e9d59dd5cb`。

# 投影证据图：便携、材料无关、默认无参考答案

每份材料的必备总览由[`plot_dft_bands.py`](DFT-PLOTS.md)生成：`02_band/`入口提供DFT能带和全部元素×spdf投影的PNG/PDF，默认包含全已算能区，可根据当前物理问题追加明确范围或窗口上下文。`dft_workflow collect --stage band`会自动生成这些图，复用已有cache时也会核对或补齐。每次选择的 `selection.png/pdf` 默认生成；[跨材料选择图集](SELECTION-GALLERY.md)集中呈现各阶段。以下`plot_review.py`用于另一个新目录中的分通道投影谱和同态竞争细节，不替代必备总览。

`tools/plot_review.py` 只读本次 DFT 的 charge-only NPZ 缓存，复用 `tools/orbital_evidence.py` 的 `read_cache`。缓存格式见 [REFERENCE-TOOLS.md](REFERENCE-TOOLS.md)。不导入开发用 reviews、材料列表、历史基组、历史窗口、已拟合的 Wannier 结果，也不执行远程作业。需要 Python 3、NumPy、Matplotlib；缓存读取器还依赖包内 `policies/reference-v0.2.json`。无显示器的服务器使用 Matplotlib Agg，直接保存图。

从包根目录运行（参数里的 `X`、`Y` 是说明用占位符，实际组名从缓存读取）：

```bash
python tools/plot_review.py --cache current_path.npz --out-dir current_review
python tools/plot_review.py --cache current_path.npz \
  --groups X:s,X:p,Y:d --comparison-basis X:p,Y:d \
  --basis-revision B1 --basis-reason 'This run: reviewed target-manifold projections' \
  --out-dir current_review_B1
```

默认检查所有已计算投影态，没有金属/绝缘体的固定种子。需要局部诊断时，用`--inspect-range EMIN EMAX`明确指定。所有能量使用缓存独立记录的EF作为零点，单位eV。

`--out-dir` 必须是尚不存在或为空的目录；非空目录会直接拒绝，避免旧轮图、CSV 和新 provenance 混在一起或被覆盖。每一轮使用单独输出目录。

## 输出与解释

每个元素×s/p/d/f 输出 `projection_*.png/.pdf`：全计算范围（或指定显示范围）的 raw fatbands、明确的局部视图（无指定域时不猜测EF范围；有decision时显示frozen上下文）、同组路径投影求和谱。**所有通道用同一个 raw weight→点面积系数，不自身归一化。** 默认点面积为 `100 pt² × raw weight`，图上附 0.1、0.5、1.0 标尺。零权重仍有灰色 DFT 背景点。图形使用散点，不声称按固定带序已追踪交叉处的连续物理分支。

指定本轮 B 后，候选通道另输出 `competition_*.png/.pdf`，每个点严格来自同一个 `(k,n)`，横轴分别为 B 中最强单组和 B 合计，纵轴为候选原始权重。对角线只是相等线，不是增加/删除轨道的阈值。只有 B 外、且在 `--groups` 中的通道生成候选图；评估 B 内成员应另外明确构造 B 去掉该成员的对照，不能隐式自我比较。B 的版本和理由写入 provenance。

数值文件：

| 文件 | 内容 |
|---|---|
| `raw_states.csv` | 所有组的原始权重、1-based k/band、E−EF、绘图横坐标；不限图形显示范围 |
| `path_projected_spectrum.csv` | Gaussian 展宽后的各投影求和谱 |
| `path_sampling.csv` | 每个 k 的未归一/归一后的求和权重和横坐标 |
| `same_state_competition.csv` | 可选；全已计算域或AI明确指定域中的候选同态比较，分母为零时 ratio 留空，不用无穷大提名 |
| `provenance.json` | 输入/脚本/输出哈希、EF 来源、平滑、权重、几何、B、候选窗口与依赖版本 |

## 路径坐标与分段

现有缓存可含 `kcoords`；仅有坐标而无坐标约定、倒格矢和段边界，不能恢复可靠高对称路径距离。默认退回 **Stored k-point index**，图上明确标注，不伪造高对称标签，也不连接跨段线。使用 `--geometry path_geometry.json`，或缓存中额外的 JSON 字符串键 `path_geometry`，才能采用真实路径长度：

```json
{
  "kcoords_type": "fractional",
  "reciprocal_lattice_2pi_per_angstrom": [[1,0,0],[0,1,0],[0,0,1]],
  "segments_1based": [[1,40],[41,80]],
  "ticks": [
    {"index_1based":1,"label":"G"},
    {"index_1based":40,"label":"X"},
    {"index_1based":41,"label":"L"},
    {"index_1based":80,"label":"G"}
  ]
}
```

上面的矩阵和端点全是格式示例，必须换成本次结构与实际存储 k 点的值。倒格矢按**行向量**给出，包含 `2π`，单位 Å⁻¹；分数坐标按 `k_fractional @ reciprocal` 转换。也可指定 `kcoords_type: cartesian_inv_angstrom`，此时不再变换坐标。`segments_1based` 为含两端的索引，必须按顺序完整分割每个存储点，每段至少两点，不能把未知断点自动接起来。若上游去重了共享端点，应在适配缓存时恢复逐段端点；当前工具不猜测共享端点连接。每段独立累积距离，跨段跳跃不计入长度；同位置的不同端点标签以 `|` 合并。

## 投影求和谱不是 DOS

计算的是

`S_alpha(E) = sum_k q_k sum_n w_alpha(k,n) G_sigma(E−E_kn) / sum_k q_k`。

默认 `sample_equal`：每个存储 k 点等权，重复端点保留。可选 `--spectrum-weight path_arclength`：必须给出上述完整物理几何，使用各段梯形积分弧长权重。不会使用线模式 PROCAR 中通常为零的 k weights 假装 BZ 积分。

`--sigma` 默认 0.10 eV，为 Gaussian **标准差**，不是 FWHM；`--points` 默认 1600。能量网格覆盖所有已计算态并向两端各延伸 `5 sigma`。`--energy-range` 只裁图，不改变投影求和的分母或输入带数。特别宽的计算能域应增大 points 并比较平滑敏感性。有限能量离散化和 Gaussian 尾部会造成少量积分误差。

这是一维路径/样本诊断，不能称为完整 BZ DOS，不能据此证明漏采样口袋不存在，也不能把谱图平滑生成的长尾当作真实目标流形边界。70% 与长尾判据仍由参考流程审阅，工具不自动设定窗口。

## 当前窗口与验收边界

`--decision current_decision.json` 仅读取显式提供的**本轮候选** dis/frozen，叠加横线；记录中的 `evidence.source_sha256` 必须匹配本次 PROCAR。工具不扫描同目录寻找历史文件，也不自动从 decision 加载 B。叠线不代表 decision 已通过 schema、计数或拟合验收；先使用 `orbital_evidence.py check-record` 做相应核验。调用方负责确保输入确实属于当前独立试验，而非参考答案。

本 CLI 不接受历史 Wannier 曲线；本轮 DFT-Wannier 拟合叠图及误差诊断由包内独立工具提供，输入格式与能量、路径、带匹配边界见 [FIT-PLOTTING.md](FIT-PLOTTING.md)。不能把这里的投影图计为 Wannier 拟合验收通过。独立测试时，运行 AI 应只拿到干净包、本轮 DFT 数据及允许的先验；图和 provenance 作为本轮输出交给人类复核。
