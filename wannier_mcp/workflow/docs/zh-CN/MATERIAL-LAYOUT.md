> 中文阅读译本；执行主版本： [English](../MATERIAL-LAYOUT.md)。
> 对应英文来源 SHA256：`410a87f7b05c10d3f54c9f453ca84d6d36aa93c28d12edfe3cb1976626a66f89`。

# 每材料的统一工作目录

每个材料都使用下面的三阶段目录。复用已有DFT也必须保留01/02入口，不能只把一堆Wannier试次放在材料根目录。同一实验根目录会默认生成[跨材料选择图集](SELECTION-GALLERY.md) `selection_gallery/index.html`。

```text
material/
  README.md
  MATERIAL-LAYOUT.json
  01_scf/README.md, SOURCE.json
  02_band/README.md, SOURCE.json, charge.npz, fit_path.json
    plots/dft_bands.png, dft_bands.pdf, projected_bands.png, projected_bands.pdf
    dft_bands.png/pdf, projected_bands.png/pdf  -> plots中的相对链接
  03_wan_trial/
    index.html                各阶段选择图与记录入口
    initial/                  保守基线JSON、selection.png/pdf、STAGE.json
    trial_001/
      README.md
      records/selection/      real_first_try或retry的封存记录、图
      records/results/        真实运行结果与评估快照
      technical/restart_NNN/  同方案技术重跑（按需）
      interface/              本次VASP接口输入与实际输出
      wannier/                正式执行器的输入、输出与wannier_run.json
      comparison/             同点比较、fit_review.png/pdf、数值诊断
      wannier90.win、wout、chk、*_hr/r/tb.dat、*_band.dat/kpt 等入口链接
      fit_review.png/pdf      -> comparison中的相对链接
```

## 复用DFT与新算DFT

```bash
python tools/material_layout.py init --case-dir /work/material \
  --scf-source /inputs/current/scf --band-source /inputs/current/band

# 接下来按DECISION-STAGES.md封存initial并select trial_001。
```

`init`要求全新case目录和已有源目录。在01/02各写README、SOURCE.json以及指向指定绝对来源的`source`目录链接；SOURCE.json记录核心文件SHA256、缺项和来源，不认证DFT收敛。来源保持只读。已有band `charge.npz`和`fit_path.json`时，以`source/<文件名>`相对链接暴露在02_band根；缺少时由正式适配器生成本轮缓存，不能手写EIGENVAL解析器。核心文件若通过软链接逃出明确指定的来源目录则拒绝，先显式提供可审计的素材目录。

新DFT由`dft_workflow.py prepare`创建01_scf、02_band，再调用库函数`register_fresh(case_dir)`登记同一目录布局；此时01/02本身就是计算目录，不存在`source`链接。SOURCE.json是准备时输入快照，后续完成和输出身份以阶段run.json为准。DFT CLI的阶段参数仍是`scf band`，不是目录名；旧目录兼容与新目录创建由DFT工具处理，不手工迁移运行中的作业。

常规由[阶段选择工具](DECISION-STAGES.md)的select创建trial；`new-trial`仅为底层目录预约，不是方案选择或执行许可。它仅创建`03_wan_trial/trial_001`、README及records。**不提前创建interface、wannier、comparison**，让各正式工具执行“必须新目录”的检查。试次编号只能为`trial_`加至少三位数字，不能覆盖旧试次。命令返回明确的`paths`：scf_dir/band_dir、band_cache/band_path、band_plots_dir、interface_dir、wannier_dir、comparison_dir、records_dir、analysis_path和decision_path（均在records/selection中）；AI按返回路径操作，不另造目录结构。

02_band图由`plot_dft_bands.py`生成；新DFT的band collect会自动调用。复用缓存时执行：

```bash
python tools/plot_dft_bands.py --cache /work/material/02_band/charge.npz \
  --path /work/material/02_band/fit_path.json --out-dir /work/material/02_band/plots
```

接口与W90执行分别见[接口](WANNIER-INTERFACE.md)和[解纠缠](WANNIER-EXECUTION.md)。正式同点对比见[拟合对照](WANNIER-COMPARISON.md)：

```bash
# submit默认预览脚本；--execute提交，由计算节点执行postw90。
python tools/compare_wannier.py submit --case-dir /work/material --trial-id trial_001 \
  --site sites/slurm.example.json --postw90 /absolute/postw90.x --ntasks 36 --execute
python tools/material_layout.py publish --case-dir /work/material --trial-id trial_001
```

`compare_wannier`完成后也会自动publish。新W90输入固定`num_iter=0`，四个输出开关`write_hr`、`write_rmn`、`write_tb`、`bands_plot`均为true。原生band图的数据采样不承诺与DFT相同；正式对比仍在确切DFT点上用postw90计算，不能自行按行对齐。

## 发布的含义和保护

`publish`只建立人类入口的相对软链接并更新工具生成的README，不复制大文件、不移动计算，也不验收科学结果。它列出执行记录中的num_iter、实际解纠缠状态和拟合状态；缺记录时写unknown，不凭目录或文件名猜参数。程序完成、解纠缠收敛、产物齐全、拟合验收四层分开，返回始终`accepted=false`。

展示必需项包括win/wout/chk、hr/r/tb、band.dat/kpt、fit_review两图、DFT纯能带/投影能带四图及wannier_run.json。缺少或空文件明确返回`incomplete`和列表；`display_complete`仅表示展示清单齐全。每次检查可幂等重跑，不能覆盖不同目标的链接、已有实体文件或非本工具管理的README。来源产物软链接不得逃出对应wannier/comparison/plots目录；管理目录也不得被软链接替代。个人笔记放records，避免写进自动更新的README。
