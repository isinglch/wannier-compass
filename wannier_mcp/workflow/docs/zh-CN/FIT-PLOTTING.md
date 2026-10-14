> 中文阅读译本；执行主版本： [English](../FIT-PLOTTING.md)。
> 对应英文来源 SHA256：`3914f72aa3cb193df3433a0cbb459100ea1237fba481ea1ffa017ba4288e558f`。

# 本次试算的 DFT / Wannier 拟合检查图

`tools/plot_fit_review.py` 读取调用者明确提供的本次输入，根据当前窗口输出frozen上下文、outer上下文两幅对照及可追溯JSON。它不会扫描目录寻找旧 `.win`、`OUTCAR`、历史窗口或成功模型。工具不选择轨道，也不返回拟合通过结论。

## 输入与调用

依赖 Python 3、NumPy、Matplotlib，以及同目录 `orbital_evidence.py` 和其使用的本包策略 JSON。服务器无图形桌面时使用 Agg 后端。

```bash
python tools/plot_fit_review.py \
  --cache current_projection.npz \
  --wannier-dat current_band.dat \
  --wannier-kpt current_band.kpt \
  --wannier-energy-reference absolute \
  --path current_path.json \
  --decision executed_windows_for_plot.json \
  --out-prefix figures/current_fit
```

DFT NPZ 沿用 [证据工具输入约定](REFERENCE-TOOLS.md)，额外要求 `kcoords` 数组，形状 `(1, nk, 3)`，值为与 Wannier 相同倒格基底下的分数坐标。不能仅凭能量或总路径长度确认对齐。

Wannier 输入使用标准 `_band.dat`（每条带一块，块间空行，两列：路径距离、能量 eV）和 `_band.kpt`（首行整数点数，后续每行分数坐标 `kx ky kz [weight]`）。此配对布局也见 [PythTB 的 Wannier90 原始读取实现](https://pythtb.readthedocs.io/en/v1.8.0/_modules/pythtb.html#w90.w90_bands_consistency)。不支持额外投影列、折叠成无空行单块的数据或其他方言；应显式转换后再调用。

`--wannier-energy-reference` 必填：

- `absolute`：Wannier 能量使用与 DFT 相同的绝对零点，统一减去缓存中的 EF。
- `relative_to_ef`：Wannier 输入已减去缓存中同一个 EF，不再次平移。

不会拟合能量偏移，也不会分别寻找两个 EF。若试算引用不同 DFT 势能零点，调用者必须先解决来源问题。默认显示`[frozen_min−1, frozen_max+1]`、`[dis_min−3, dis_max+3]`，单位eV且相对同一SCF EF。`--frozen-padding`/`--outer-padding`可显式改变画图留白；`--focus-range`/`--full-range`可增加调用者明确声明的视图。没有窗口时仅显示全部已供给态，不猜测EF区间。

## 分段文件与拒绝条件

`current_path.json` 使用零起点、右端不含的 Python 切片。每段显式记录其均匀直线路径端点和标签；以下只是格式示例：

```json
{
  "coordinate_system": "fractional_reciprocal",
  "segments": [
    {
      "start": 0, "stop": 21,
      "start_fractional": [0, 0, 0], "end_fractional": [0.5, 0, 0],
      "start_label": "G", "end_label": "X"
    },
    {
      "start": 21, "stop": 42,
      "start_fractional": [0.5, 0, 0], "end_fractional": [0.5, 0.5, 0],
      "start_label": "X", "end_label": "M"
    }
  ]
}
```

连续两段可以各自保留重复端点，也可以共享一个端点（下一段 `start=上一段 stop−1`）。不连续段单独画线，不跨断点连接。端点来自本次路径定义；不得从历史成功模型推断。

工具逐点核对 DFT 与 Wannier 的坐标、数量及顺序；每段都与声明的均匀直线核对，并检查所有点被分段完整覆盖。默认坐标容差 `1e−6`，可显式调整，但不得超过 `1e−4`。不会按倒格矢平移、重新排序、删除端点、按总长缩放或插值伪造匹配。VASP 和 Wannier 的默认路径离散方式可能不同；不匹配时退出 2，需让本次 Wannier 在确切 DFT 点序上重新求值或重新准备相同采样的比较输入。未经对齐不会给误差。

画图共用 `_band.dat` 已有距离坐标；它只影响横轴显示，不能代替分数坐标检查。多个 Wannier 带块必须使用同一距离序列。该工具当前只接受均匀直线分段；非均匀路径应另建明确适配器，不能放宽检查绕过。

## 可选误差与窗口标记

默认只有叠图，JSON 为 `status: overlay_only`、`metrics: null`、`fit_pass: null`。不自动做 nearest-band 匹配。

如已审阅出一一对应关系，显式传入 `--mapping current_mapping.json`：

```json
{
  "pairs_1based": [[3, 1], [4, 2]],
  "reason": "本次目标范围内逐带核对后确认的固定索引对应",
  "evidence_refs": ["current_mapping_review.md"]
}
```

两列分别是输入 DFT 带号和 Wannier 带号，从 1 开始；同一列禁止重复，避免多条 DFT 带被同一条 Wannier 带重复匹配。仅对这些配对报告全路径，以及已提供的实际frozen/outer区间的MAE、RMSE、最大绝对误差，按采样态等权。区域掩码以DFT能量定义；没有配对态时统计为null。显式focus范围单独标为requested_detail，没有默认近EF指标。未映射带不属于误差范围。这不是布里渊区积分；重复端点也保留在统计内。

映射是调用者提供的科学判断，工具只检查索引范围与一一性，不证明交叉处波函数连接。固定带号无法表达跨交叉的换序；这类情况需分支审阅或以后实现有重叠矩阵依据的映射，不能依靠最近能量降低误差。即使提供映射，`fit_pass` 仍为 null；验收范围和容差由本次任务另行声明。

可选 `--decision current_decision.json` 只读取 `windows.dis_eV` 和 `windows.frozen_eV` 作图，并检查有限值、顺序和嵌套关系；不证明该 decision 已完成基组审阅，也不替代 `check-record`。缓存可来自能带路径，而 decision 可能来自实际 Wannier 网格，因此此处不伪装成同源执行验证。调用者要保证都是本次试算并采用同一个 EF，JSON 保留完整输入文件哈希供核查。

## 输出与边界

输出 `<prefix>.png`、`.pdf`、`.json`。JSON 保存全部显式输入的 SHA-256、EF 来源、能量参考、坐标核查、分段、显示范围、窗口标记与可选映射误差。输出已存在时拒绝覆盖，换新的前缀即可保留各轮证据。

只检验给出的路径采样；不覆盖路径外、子空间秩、波函数、对称性或输运响应。没有历史材料名、基组表或窗口查表，也没有作业提交能力。开发仓库的 `selftest/test_fit_review.py` 包含错序、缺点、错误分段、多对一映射拒绝，以及相同零点下的图像输出测试；该合成测试文件不随通用运行包分发。

JSON的`display_ranges_eV`记录实际绘图上下界；`metrics.regions_by_dft_energy`记录误差区域。两者必须区分：默认±1/±3 eV是图形上下文，不是拟合容差，也不是新增应精确复现的目标态。
