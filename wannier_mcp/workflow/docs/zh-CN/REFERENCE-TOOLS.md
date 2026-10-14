> 中文阅读译本；执行主版本： [English](../REFERENCE-TOOLS.md)。
> 对应英文来源 SHA256：`fb41dc0c7a0276fafa68b42570702b38f7faf1babfdce6a44984f0a74febed61`。

# v0.2 证据工具与决策记录

执行阶段以[initial与real_first_try](DECISION-STAGES.md)为准：独立propose只作草稿；新执行必须绑定封存的real_first_try/retry。initial不直接执行，旧结果仍可只读核查。

完整选择入口为 [manifold_selection.py](MANIFOLD-SELECTION.md)，低层证据/校验入口为 `tools/orbital_evidence.py`，跨材料人工审阅入口为 [`tools/selection_gallery.py`](SELECTION-GALLERY.md)；参数为 `policies/reference-v0.2.json`，记录格式为 `policies/decision-v0.2.schema.json`。它们服务于 [完整流程](REFERENCE-WORKFLOW-V0.2.md)，没有独立的材料阈值表。本包 MCP 服务封装相同的 CLI，不另设选择算法；科学 CLI 的 v0.2 契约标识与 Python 包版本分别记录。

重复试算可用`tools/shared_nscf.py seal/verify`封存一次完整网格WAVECAR，再用`tools/prepare_wannier_interface.py prepare --shared-source DIR`生成每轮基组对应的`ALGO=None`接口；详见[共享波函数入口](WANNIER-INTERFACE.md)。这不改变投影band选择依据或`num_iter=0`执行契约。

每材料先用[目录工具](MATERIAL-LAYOUT.md)登记`01_scf/02_band/03_wan_trial`，initial/select返回阶段路径，analysis/decision放trial的records/selection。02_band保留总能带/投影图入口；程序输出与拟合图分别在trial/wannier和trial/comparison，publish提供根目录相对链接，不改变科学状态。

## 1. 已有PROCAR缓存适配器

`analyze`读取NPZ，不直接读取裸PROCAR。当前接受单个电荷通道，要求：

| 键 | 内容 |
|---|---|
| `metadata` | JSON字符串，含`species`、`counts`、`columns`、独立`ef`/`ef_source`、`procar_sha256`，可含`name` |
| `energies` | 绝对能量数组，形状`(1,nk,nb)`；工具减去metadata中的EF一次 |
| `raw` | 原始电荷投影，形状`(1,nk,nb,nion,norb)`，norb=9或16；不能混入SOC磁化表 |
| `metadata.columns` | `s, py, pz, px, dxy, dyz, dz2, dxz, x2-y2`，可再包括[VASP-CACHE](VASP-CACHE.md)规定的完整7个f列；缓存列顺序可不同，不包含`tot` |

已有缓存可包含额外的k坐标、权重、occupation等键；当前同态工具不计算路径积分，也不自动判定分支连续性。[vasp_to_cache.py](VASP-CACHE.md)提供普通scalar/SOC原始PROCAR转换，并核对EIGENVAL、POSCAR和独立EF来源；不能直接把不符合格式的缓存改名来调用。`spinor`与`scalar`必须由调用者依据实际计算显式指定；完整resolved f列已支持；当前仍不支持同时处理两个共线自旋通道。

## 2. 原始提名及同态比较

在`auto_wannier/`目录执行：

```bash
python3 tools/orbital_evidence.py analyze \
  --cache /path/to/charge_cache.npz \
  --spin-mode spinor --sampling band_path --out evidence.json
```

不指定B时，只给绝对权重分级，`competition=null`。B尚未建立不等于该候选没有竞争成分。

原始权重0.10只是可见候选提示，不是自动`include`线。不能先把所有达到0.10的通道装进B，再利用B成员不参加额外候选比较来跳过竞争复核。每个最终纳入的通道，包括主体B中的成员，都须说明其对目标分支的独立贡献或描述混合态的必要性；“超过0.10”本身不构成依据。不新增固定比值阈值，工具已对每个B成员自动去掉自身再比较，并保存实际comparison_channels。

主体组已经审阅时，再添加：

```text
--comparison-basis X:p,Y:d
--basis-revision B1
--basis-origin projected_bands_review
--basis-reason <当前目标分支支持这些主体通道的说明>
--basis-evidence-ref <投影图或逐态证据文件>
```

示意元素必须替换为缓存中的实际元素。对明确的历史固定基组诊断，使用`--basis-origin historical_control`，不能将这种控制回放报成自动识别成功。B中的成员自动与B中其余成员比较，自身不进入分母；只有改变整个B的选择时才变更版本并重算。

输出包括全部通道的原始分级、触发态的每组原始值，全部能量范围的原始峰、逐带均值，以及所有候选对B的同态竞争。默认全谱输出`unclassified_full_spectrum`，不使用局部浓度自动提名轨道；显式诊断区可附软标签，但不产生选择结论。统计同时列出原始0.05和0.10支持范围，不用哪个范围更像历史来选择规则。分母为零的比值用null并单独计数；主体通道全部很弱时，保留原始数值供AI判断投影证据是否不足。

`analysis_id`绑定输入缓存、可选诊断域、采样/自旋约定、B及其版本和依据、政策哈希、计算结果。B按固定顺序计算，参数中排列顺序不改变结果。工具始终返回`needs_basis_review`，不把候选变成最终基组，不自动连接跨交叉的固定带号。

## 3. AI决策记录

对每个通道记录`include`、`omit_first_trial`或`defer`，以及理由、可定位的证据引用。`trial_basis`严格等于所有`include`组；`defer`不会自动并入基组。记录中证据的可定位性和物理解释由AI审阅负责，Schema只约束引用存在且非空，当前CLI不联网读取引用或判断理由是否成立。

| 状态 | 含义 |
|---|---|
| `needs_basis_review` | B、额外通道或关键分支仍待审阅 |
| `basis_trial_candidate` | 已形成一个首轮基组，尚未完成窗口审阅 |
| `needs_window_review` | 分母、尾部、边界等窗口证据待审阅 |
| `needs_more_data` | 记录实际缺失数据及需要补充的步骤 |
| `proposed_trial` | 已审阅的基组/窗口通过所给采样的必要预检查；band路径允许准备接口，实际mesh检查仍待接口输出，不代表拟合通过 |

歧义需要有限试算时，AI可分别为每个候选写一条自洽记录；不能在同一条记录里留着`defer`却声称已经确定最终试算配置。

band的`proposed_trial`要求参考域已由投影分布和目标范围审阅，记录为`reviewed_target_manifold`并提供`complete_band_ranges_1based`；不要求自动分块算法已经解决。仅有`all_computed_diagnostic`统计时，计数通过也不能代替窗口审阅。默认band路径候选使用`next_action=prepare_interface`，并显式记录`numerical_checks.sampling=band_path`和`mesh_gate_pending=true`，然后运行一次接口NSCF取得实际mesh证据。缺少预计算mesh不是阻塞原因，也不要求先collect/analyze另一份mesh。只有真实缺失band/SCF输入才报告缺项；只有确认带数截断才使用`add_bands_nscf`。

## 4. 校验与重算

```bash
python3 tools/orbital_evidence.py check-record \
  --cache /path/to/charge_cache.npz \
  --analysis evidence.json --record decision.json
```

校验Schema、analysis/source/policy身份、B版本、通道决定和基组的一致性；另复算显式目标带组的完整能量包络，若outer切掉它则拒绝band提案，不能用Nouter足够替代完整性。若有窗口，重新计算当前缓存上的`Nouter`、`Nfrozen`、完整s/p/d/f基组的`num_wann`及最高计算带的共同顶部余量；拒绝错序/不嵌套窗口、陈旧检查以及与重算结果矛盾的`gate_pass`。

窗口检查限定于所给缓存。`band_path`可以进入`proposed_trial`并允许准备接口，但返回`mesh_gate_pending=true`，不能据此进入解纠缠。真正网格计数来自本次接口的EIGENVAL/.eig及实际k点。已有网格的`mesh_fallback`/`trial_wannier`仅为兼容分支，须核对其物理设置，不能变回默认前置。本工具不检查完整BZ、波函数投影秩或插值精度，支持经过占据、带数和采样全局隙审阅的低能前缀`exclude_bands`（见[选择契约](MANIFOLD-SELECTION.md)）；任意高带删除或exact-square输入生成尚不属于此入口。

输出`record_consistent=true`表示记录与本次采样的必要数学检查一致。另读`check_scope`、`mesh_gate_pending`、`interface_preparation_allowed`、`ready_for_wannier`，独立check-record的interface_preparation_allowed仅表示数值预检查可用；实际执行还要求封存的real_first_try/retry。不能把数值通过等同于阶段选择或允许解纠缠。没有`validated_model`或`fit_pass`状态。MCP 调用层同样须在实际网格、输入文件和作业范围上核对同一配置，不能将本命令退出码当作已验证模型。

## 5. 验证边界

证据CLI不执行DFT或Wannier90。接口的`prepare`只准备输入，`submit-interface`只预览/生成脚本，带`--execute`才提交，`run-interface`才实际执行；`validate --outputs`只检查已经存在的输出。具体职责见[接口入口](WANNIER-INTERFACE.md)，后续解纠缠统一使用[Wannier执行入口](WANNIER-EXECUTION.md)。不能以校验代执行，也不能覆盖执行记录或manifest使输出自证一致。

解纠缠CLI是`tools/wannier_workflow.py`：`prepare --interface-dir DIR --out-dir NEW`从通过检查的接口生成独立执行目录；`submit --directory NEW --site PROFILE [--execute]`预览或提交；`run`在计算分配中执行；`inspect --directory NEW`只读检查。实际执行记录为`wannier_run.json`，输入清单为`wannier_manifest.json`，不是用校验覆盖接口执行记录。若真实DIS与postwann chk已完成、仅输出或最终哈希被墙时中断，使用`prepare-output-recovery --source-dir SOURCE --out-dir NEW --source-job-id JOB`及`submit-output-recovery`；它固定`restart=plot`、Slurm `afterany`、新技术目录和来源稳定哈希，依赖启动时若来源已被常规流程完整认证则no-op。`inspect`自动区分普通与恢复manifest；恢复目录的DIS上下文只能来自来源WOUT，恢复WOUT必须无DIS历史。可从`sites/slurm-output-recovery.example.json`建立本地恢复配置，其资源时间和节点排除由部署者填写。

使用包内解析与计数器，不另写EIGENVAL解析或手填`num_wann`；保留VASP生成的`.win`，不从EIGENVAL/manifest重新拼造它。新输入固定`num_iter=0`，四个输出开关`write_hr`、`write_rmn`、`write_tb`、`bands_plot`均为true。分开报告W90程序完成、实际解纠缠收敛、本轮chk/hr/r/tb/band产物齐全、拟合验收四层状态；文件齐全、程序退出0或`All done`不能替代后两类科学检查。

正式拟合入口为`python tools/compare_wannier.py submit --case-dir /work/material --trial-id trial_001 --site sites/slurm.example.json --postw90 /absolute/postw90.x --ntasks 36 --execute`；由提交的Slurm作业执行，详见[同点比较](WANNIER-COMPARISON.md)。它创建comparison并生成/发布两张拟合图与误差诊断；不需要AI临时编写driver。原生bands_plot路径只作产物与展示，后续段采样密度由W90按长度缩放，不能冒充确切DFT点匹配。

开发仓库中的`selftest/test_reference_evidence.py`用合成数据检验同态分母、弱通道、零分母、状态与陈旧记录的处理；该测试文件不随通用运行包分发。这些是程序行为测试，不是材料拟合验收。

历史材料回放沿用已有NPZ，比较新增工具与独立逐态计算的数值；没有把历史基组当成新材料选择器，也没有新增DFT/Wannier作业。

不再要求`--seed`。默认统计全已计算谱、逐带原始分布和同态比较；若AI已有物理依据，可用`--inspect-range EMIN EMAX`追加指定区间诊断。旧`--seed`仅为显式参数别名，无默认值。`seed_eV=null`表示没有该局部域，不能解释成EF附近无权重。scope中的`priority_window_eV`默认null，额外精度要求必须由当前任务声明。

完整resolved f列现可按元素×f汇总、审阅和计数，格式见[VASP-CACHE](VASP-CACHE.md)。共线双谱、未知/不完整f列仍明确拒绝。
