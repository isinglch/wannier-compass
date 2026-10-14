> 中文阅读译本；执行主版本： [English](../SELECTION-GALLERY.md)。
> 对应英文来源 SHA256：`10840999129ff9a42808df53e54961269497e129916c7d215ebb01f4e463fc72`。

# 选择图集：默认出图、集中人工审阅

每次 `manifold_selection.py propose` 都生成正式 `selection.png/pdf`、`selection_plot_manifest.json`，并在输出目录保存本次的 `inspection.json`、`review.json`；manifest 将两张图的哈希绑定到这一次 `selection.json`。当输出目录按 `案例/initial`、`案例/real_first_try` 或 `案例/retry_NNN` 命名时，默认同步更新这些案例共同父目录下的选择图集；特殊用途可传 `--no-gallery` 只跳过图集，单次选择图仍照常生成。每次 `decision_stages.py initial/select` 也默认生成并封存图，更新本材料 `03_wan_trial/index.html`，并在**同一实验根目录**更新 `selection_gallery/index.html`。图集仅引用文件，不复制大图或改变科学决定。

实验根目录的直接子目录应是各材料目录：

```text
experiment/
  material_A/MATERIAL-LAYOUT.json, 01_scf/, 02_band/, 03_wan_trial/
  material_B/MATERIAL-LAYOUT.json, 01_scf/, 02_band/, 03_wan_trial/
  selection_gallery/index.html, gallery_manifest.json
```

自动图集只收集同一个 `context.json.experiment_id` 的材料，跳过其他实验。`gallery_manifest.json` 记录每个阶段的方案/图片哈希和可见问题；阶段本身的 `STAGE.json` 仍是封存身份依据。可手动刷新：

```bash
python tools/selection_gallery.py --root /absolute/experiment --experiment-id current-run
```

独立 AI 只做选择、还未建立材料目录时，也可直接汇总明确给定的测试输出根。它只扫描根目录的直接子目录及其 `initial`、`real_first_try`、`retry_NNN`，不会读取旁边的历史目录：

```text
test-results/
  case-0001/initial/selection.json, review.json, selection.png, selection.pdf
  case-0001/real_first_try/...
  case-0002/initial/...
```

```bash
python tools/selection_gallery.py --root /absolute/test-results --mode selection-only
```

标准命名的 `propose` 默认输出 `/absolute/test-results/selection_gallery/index.html`。使用其他目录名或需要手动刷新时，运行上述命令。其他 AI 提交选择结果时，应把图集、`gallery_manifest.json` 和原始各阶段记录一起交给人工检查。新 `propose` 的图片哈希会由图集核验；旧记录若没有 `selection_plot_manifest.json`，图集明确标为 `unsealed_without_figure_manifest`，不能声称图片和方案已哈希绑定。正式阶段则由 `STAGE.json` 核验。图集中同时链接 DFT/投影能带图（如果本轮已生成）、选择图 PNG/PDF、`review.json` 和 `selection.json`；缺文件、篡改或身份不一致会显示警告，不会静默当作成功。

图集是**选择可视化**：它不批准排带的实际接口网格、Wannier 收敛、`hr.dat` 产物或能带拟合。默认出图不等于自动扩大 frozen，也不会从历史答案给 AI 提示。
