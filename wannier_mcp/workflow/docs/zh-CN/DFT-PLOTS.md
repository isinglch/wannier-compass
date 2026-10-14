> 中文阅读译本；执行主版本： [English](../DFT-PLOTS.md)。
> 对应英文来源 SHA256：`3e8141e70777d978c85d3a1e0500c434305b9a372bdede6dd3fb7ebb67e73c90`。

# 每份材料的 DFT 能带与分轨道图

完成或复用已有投影 band 数据后，均应在该材料的 `02_band` 中提供 DFT 能带和完整元素×壳层投影图。画图读取现有 band 缓存，不增加 DFT、mesh 或 PDOS 计算，不改变轨道和窗口选择规则。

```bash
python tools/plot_dft_bands.py \
  --cache /absolute/material/02_band/charge.npz \
  --out-dir /absolute/material/02_band/plots \
  --title Material
```

已有`fit_path.json`时也可加`--path /absolute/material/02_band/fit_path.json`作额外身份核验；否则直接使用band缓存内已验证且带原始KPOINTS哈希的路径几何。缺失或不一致时失败，不按采样点序号假造高对称路径。`--out-dir` 必须是新目录；不会覆盖以前的图。若原始输入仍在已准备的 `band/`，可直接读取那里，把新图放到规范的 `02_band/plots`。不要复制并重新解释 EIGENVAL 或重建 k 路径。

输出：

- `dft_bands.png`、`dft_bands.pdf`：同一实际DFT路径，默认显示全部已算能区；可增加AI明确指定的视图。
- `projected_bands.png`、`projected_bands.pdf`：每个元素×s/p/d/f 一行，每行使用相同的已声明展示范围。包括零权重通道，不自动隐藏或筛选。
- `manifest.json`：源缓存和路径哈希、PROCAR 标识、SCF EF 来源、展示范围、原始权重尺度、真实路径距离、分段、图片哈希及适用范围。

不再默认EF±10或固定近EF视图。第一列始终保留全部已算能区。AI可用`--energy-range EMIN EMAX`或`--focus-range EMIN EMAX`添加有物理依据的诊断范围；有当前窗口时，传入`--decision FILE`会追加frozen上下各1 eV、outer上下各3 eV视图。展示范围只控制画图，不构成轨道选择、拟合目标或验收条件。

投影权重直接取现有 charge-only PROCAR 缓存，按同元素所有原子和完整 s/p/d/f 壳层的磁量子数分量求和。默认散点面积为 `48 × raw_weight` pt²；所有通道使用相同线性色标，默认覆盖 0 到 1，若数据超过 1 则所有通道共同扩展上限。没有按各通道最大值归一化、没有按原子数平均、没有截断权重、没有用弱权重通道自己的最大值将其放大。

图标题显示通道全路径最大原始值；manifest 另保留各声明展示范围的原始最大值和全采样态原始权重总和。该总和不是 BZ 积分占据数，原子投影也不是全空间概率归一。

横轴使用缓存中的完整实际 k 顺序、含 2π 的倒易晶格行向量和 `fit_path.json` 的显式路径段。每段各自累积真实倒空间长度，段间跳跃不计入距离；不连接不连续路径段，不删除重复端点，不按坐标排序。标签采用 `fit_path.json`。缓存、PROCAR、KPOINTS 来源哈希和路径端点不一致时拒绝作图，不降级为猜测路径。

图用于人工/AI 检查 DFT 和原始投影。它不选择基组、不改变数值阈值、不宣布拟合通过。
