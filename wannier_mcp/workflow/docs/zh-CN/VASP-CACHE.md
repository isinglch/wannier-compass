> 中文阅读译本；执行主版本： [English](../VASP-CACHE.md)。
> 对应英文来源 SHA256：`f7d0312b6bd95e4b5be85740de9305d4a6020ce737c02ab61e6cc528ac9fa5e0`。

# VASP 输出到证据缓存

`tools/vasp_to_cache.py` 把**显式指定的本轮静态计算输出**变成轨道审阅、绘图和网格检查共用的 NPZ。它只解析数据，不选择材料、轨道或窗口，不扫描其他目录，不提交计算。依赖 Python 3 和 NumPy。

## 能带路径

```bash
python tools/vasp_to_cache.py \
  --poscar run/band/POSCAR \
  --eigenval run/band/EIGENVAL \
  --procar run/band/PROCAR \
  --scf-outcar run/scf/OUTCAR \
  --spin-mode spinor --sampling band_path \
  --kpoints run/band/KPOINTS \
  --fit-path-out run/band/fit_path.json \
  --out run/band/charge.npz
```

`--spin-mode` 是期望值，必须与实际 PROCAR 表结构一致。它不是用来把 scalar 数据改标为 spinor。省略时从完整表结构判断：每带一张 charge 表为 scalar；每带四张表为 spinor，只读取第一张 charge，另外三张磁化表只检查格式完整性，不相加。

本包也支持单原子 scalar PROCAR 缺少冗余 tot 行的格式；配套解析测试核对该格式的占据约定。

KPOINTS 目前支持 Reciprocal Line-mode，每段使用同一个插值点数，包含两端点，邻段共享的端点在输出中重复。工具逐点核对 EIGENVAL 中的实际坐标和声明路径，才写入分段信息。倒格矢从 POSCAR 的实空间晶格计算，包含 `2π`，单位为 Å⁻¹。不会连跨段跳跃，也不会把重复端点合并、重排或估计高对称点。

生成的 `path_geometry` 可直接被 `plot_review.py` 使用；`fit_path.json` 可交给 `plot_fit_review.py --path`。后者仍要求本轮 Wannier 路径与实际 DFT 坐标逐点一致，转换脚本不会重采样两边曲线来制造一致。

## 可选的已有均匀网格缓存（不是默认前置）

默认从上面的band缓存决定轨道与窗口，再运行一次Wannier接口NSCF。实际网格计数和矩阵校验使用该次接口输出；不要求提前计算或缓存mesh PROCAR，也不要求WAVECAR。以下仅用于已存在的兼容数据或明确另外授权的分析。

```bash
python tools/vasp_to_cache.py \
  --poscar run/mesh/POSCAR \
  --eigenval run/mesh/EIGENVAL \
  --procar run/mesh/PROCAR \
  --scf-outcar run/scf/OUTCAR \
  --spin-mode scalar --sampling wannier_mesh \
  --out run/mesh/charge.npz
```

实际 `NKPTS`、`NBANDS`、能量、坐标及权重都来自 EIGENVAL，并与 PROCAR 核对。缓存不从 `NKPTS` 猜测 `Nx×Ny×Nz`；`--sampling wannier_mesh` 只是本轮用途声明，执行/接口层还须核对实际坐标是否覆盖要求的完整均匀网格。未声明用途且未提供 line-mode KPOINTS 时标为 `sampled_kpoints`。不能把带路径标为 Wannier 网格。

## 能量参考

默认路线必须显式给出 `--scf-outcar`。它要求静态自洽标记 `NSW=0`、`ICHARG<10`，同一 scalar/spinor 模式，以及正常结束的计时标记，然后读取最后一个 `E-fermi`。这些解析检查不证明电子自洽已收敛；执行工作流必须另行完成收敛、结构和 Hamiltonian 一致性检查。工具不会使用线模式能带计算自身的 EF，也不按目录名称搜索“最像”的 SCF。

需要采用另一份已审核的、与当前 Hamiltonian/结构一致的均匀网格参考时，可以用 `--ef-json reviewed_ef.json` 代替 `--scf-outcar`：

```json
{
  "kind": "reviewed_scf_reference",
  "ef_eV": 0.0,
  "source_path": "scf/OUTCAR",
  "source_sha256": "填入来源文件的完整 SHA256",
  "spin_mode": "spinor",
  "reason": "记录为何该来源与本轮结构、SOC 和计算设置一致，以及 EF 的提取依据"
}
```

`ef_eV` 没有缺省值。相对来源路径相对于 JSON 所在目录；来源文件必须可读且哈希匹配。这个分支保存人为/AI 审核的理由，不把自报的理由当作科学验证，也不自动跨 scalar→SOC 复用能量零点。

## 保存内容与检查边界

| 项目 | 缓存契约 |
|---|---|
| `energies`, `occupations` | `(1, nk, nb)`；能量保留 DFT 绝对值，单位 eV |
| `raw` | `(1, nk, nb, ni, norb)，norb=9（s/p/d）或16（s/p/d/f）`，第一张 charge 表中逐离子原始打印权重；不自身归一、不除以 `tot` |
| `kcoords`, `kweights` | `(1, nk, 3)` 分数倒空间坐标、`(1, nk)` 原始权重；保留零权重路径点 |
| `printed_total` | `(1, nk, nb)`，原始 charge 总权重，舍入差作为诊断保留 |
| `metadata` | JSON，含 species/counts/columns、EF 来源、实际 nk/nb/ni、spin_mode、sampling、来源哈希和最大对齐差 |
| `metadata.source_files` | `poscar/eigenval/procar` 和可选 `kpoints` 的绝对路径、SHA256、字节数 |
| `path_geometry` | 可选 JSON；只有明确验证的 line-mode 路径才写入 |

所有输入必须是同一静态计算的输出。PROCAR 每个 k 点、band 和 ion 索引必须完整、递增，原子数与 POSCAR/EIGENVAL 一致；表头、表数、数值有限性和 charge 非负性都要通过。EIGENVAL 与 PROCAR 默认允许的打印精度差为能量 `2×10⁻⁵ eV`、分数 k 坐标 `2×10⁻⁷`、占据 `10⁻⁵`、k 权重 `10⁻⁷`。能量/k 容差可显式调整，但上限分别为 `10⁻³ eV` 和 `10⁻⁴`，不会无限放宽。

**PROCAR 只有 ion 编号，没有元素标签。** 转换器按显式 POSCAR 的原子顺序赋予元素；仅凭这三个文件不能证明一个同原子数、同能谱的 POSCAR 没被换过。执行层必须在运行前固定并记录 POSCAR/POTCAR 输入，在后处理中沿用同一份输入，不能用计数一致替代来源一致。缓存保留 POSCAR 哈希、原子顺序和位置方便追溯。

输入转换期间发生尺寸/mtime 变化时拒绝保存；输出文件已存在时拒绝覆盖。能量和投影失配、截断文件、乱序原子、二套共线自旋能谱、不完整/粗分轨道表、未知f列格式、复投影方言，以及不支持的路径布局都会明确报错。当前范围是普通 static scalar 和 charge＋三磁化表的非共线/SOC 输出；不解释 MD 预测本征值、KPOINTS_OPT 混合网格、collapsed/shared-endpoint 路径或动态结构。

解析约定以 [VASP PROCAR](https://vasp.at/wiki/PROCAR)、[EIGENVAL](https://vasp.at/wiki/EIGENVAL)、[KPOINTS](https://vasp.at/wiki/KPOINTS) 和 [LNONCOLLINEAR](https://vasp.at/wiki/LNONCOLLINEAR) 的文件含义为依据；这些文档没有提供自动选择 Wannier 基组或窗口的判据。

开发仓库中的合成回归测试（不随运行包分发）：

```bash
python -m unittest discover -s selftest -p test_vasp_to_cache.py -v
```

支持的f列为完整的 `fy3x2 fxyz fyz2 fz3 fxz2 fzx2 fx3`，接在原9个s/p/d列之后；缓存记录实际列名。各态、charge和磁化表须使用同一完整布局。缺少f列表示没有f投影证据，不表示f权重为零，也不会制造零f通道。完整f壳层每原子7个空间轨道，当前spinor双自旋约定计14个。
