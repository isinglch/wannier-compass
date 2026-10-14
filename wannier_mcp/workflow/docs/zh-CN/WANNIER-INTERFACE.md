> 中文阅读译本；执行主版本： [English](../WANNIER-INTERFACE.md)。
> 对应英文来源 SHA256：`3df21596a799b227327b05d6c2363fa2dcf24cda6fb1138fd4086003e800f00b`。

# 从投影 band 到单次 VASP/Wannier90 接口

执行阶段以[initial与real_first_try](DECISION-STAGES.md)为准：独立propose只作草稿；新执行必须绑定封存的real_first_try/retry。initial不直接执行，旧结果仍可只读核查。

默认使用已有SCF密度和投影band判断轨道、窗口，然后进行**一次均匀网格Wannier接口NSCF**，直接生成`.amn/.mmn/.eig`。不要求预先完成独立mesh、mesh PROCAR/cache、PDOS或WAVECAR。接口本身仍须覆盖完整均匀k网格，实际态数及矩阵检查在这次计算之后进行。

如果要在同一物理设置、k网格和NBANDS上反复测试多个基组，可以先建立一次固定密度的**共享波函数源**，再用下文的`--shared-source`入口。其后每次基组或排带改变仍运行一次VASP `ALGO=None`来生成匹配的矩阵，但不重新迭代波函数。只有窗口改变时，按[Wannier执行入口](WANNIER-EXECUTION.md)复用已有矩阵。

## 默认入口

先按[材料目录规范](MATERIAL-LAYOUT.md)执行init，再按阶段规范initial/select。以下为复用DFT的路径示例；新算DFT的scf_dir/band_dir直接位于01/02，按目录工具返回paths填写，不自行增加source。

```bash
python tools/prepare_wannier_interface.py prepare \
  --band-dir /work/material/02_band/source \
  --scf-dir /work/material/01_scf/source \
  --cache /work/material/02_band/charge.npz \
  --analysis /work/material/03_wan_trial/trial_001/records/selection/analysis.json \
  --decision /work/material/03_wan_trial/trial_001/records/selection/decision.json \
  --mesh-kpoints /work/material/03_wan_trial/trial_001/records/interface_KPOINTS \
  --out-dir /work/material/03_wan_trial/trial_001/interface

python tools/prepare_wannier_interface.py validate \
  --trial-dir /work/material/03_wan_trial/trial_001/interface
```

`--mesh-kpoints`只提供本轮接口的采样输入，不表示那个目录必须已有DFT输出。也可用`--mp-grid NX NY NZ`指定接口网格，互斥规则以CLI `--help`为准。`--band-dir`始终必需；可额外传`--mesh-dir`复用已有网格作为兼容路线，不是默认前置，也不能代替本轮band路径。默认无mesh-dir的路线不接受`--reuse-wavecar`。

## 重复试算的共享WAVECAR入口

先用同一SCF的CHGCAR，在**自动Gamma、零shift的完整均匀网格**上做一次`ICHARG=11`、`ALGO=Normal`、`LWAVE=.TRUE.`的固定密度NSCF，并预留足够NBANDS。这一步要让占据态和目标空态波函数正常收敛；只有CHGCAR、给`NELM=1`并不能替代。运行完成后封存：

```bash
python tools/shared_nscf.py seal --directory /work/shared_nscf/material \
  --scf-dir /work/material/01_scf/source --band-dir /work/material/02_band/source
python tools/shared_nscf.py verify --directory /work/shared_nscf/material
```

如果共享波函数由本包 `dft_workflow.py prepare --stages scf band mesh` 与
`run-stage --stage mesh` 生成，可以直接把案例中的 `_optional_mesh` 当作上述
`--directory`，并将同一案例的 `01_scf`、`02_band` 传给 `seal`。封存器会读取原本
就在运行前写好的 `case.json` 和执行后写出的 `run.json`，逐一复核输入哈希、
实际输出、完整 Gamma 网格及 WAVECAR；不会事后伪造旧式
`PREPARATION.json/process_exit.json`。原有独立共享源路线保持可用。

封存核对准备清单、物理设置、SCF电荷、完整均匀k网格、EIGENVAL、WAVECAR头/长度、实际NBANDS、进程退出和OUTCAR正常收敛，并给输入与输出写哈希。没有通过封存的来源不能进入共享入口。该来源只证明波函数数据身份，不证明基组/窗口或拟合正确。准备清单采用`shared-nscf-preparation-v1`，记下运行的输入哈希、网格、申请NBANDS和实际执行资源；不要把历史Wannier答案写入这里。

每个新的轨道/排带方案在**独立目录**中运行：

```bash
python tools/prepare_wannier_interface.py prepare \
  --band-dir /work/material/02_band/source \
  --scf-dir /work/material/01_scf/source \
  --cache /work/material/02_band/charge.npz \
  --analysis /work/material/03_wan_trial/trial_001/records/selection/analysis.json \
  --decision /work/material/03_wan_trial/trial_001/records/selection/decision.json \
  --shared-source /work/shared_nscf/material \
  --out-dir /work/material/03_wan_trial/trial_001/interface
```

这里不再传`--mesh-kpoints`或`--mp-grid`。准备器从封存源复制原始KPOINTS和WAVECAR，核对拷贝哈希，绝不把试算WAVECAR做成指向共享源的符号链接。新接口使用`ISTART=1`、`ALGO=None`、`NELM=1`、`LDIAG=.FALSE.`、`LWAVE=.FALSE.`、`LCHARG=.FALSE.`；仍需VASP生成基组对应的`.amn/.mmn/.eig/.win`。运行器在执行前核对试算WAVECAR；若VASP随后以零字节占位文件替代它，只有正式运行记录保存了该执行前哈希才接受占位文件，绝不把它当作新的波函数源。执行后必须在OSZICAR看到恰好一条`NONE`记录，不允许`DAV/RMM/DIA/EIG`等轨道优化/重算标记；再核对本次EIGENVAL的k顺序和逐态能量与共享源一致，以及矩阵、排带、窗口逐点态数。VASP仍做一次程序级后处理，并非零工作量；这里禁止的是重新对角化或迭代优化波函数。若共享源NBANDS高于旧投影band，来源会分别记录两者；旧band图不能提供新增高空带的轨道投影判断，必要时先补同设置的投影band证据。`--shared-source`和无来源默认接口是两条明确的执行模式；不要用旧`--reuse-wavecar`绕过身份核对。

共享 WAVECAR 测试应在私有执行配置中设置 `require_shared_wavecar=true`；它会在提交和实际执行两处拒绝普通 `ISTART=0` 接口，防止误启动新的多步 NSCF。`sites/slurm-shared-wavecar.example.json` 只是可选 Slurm 参考配置，复用波函数本身不要求该调度器。若共享源尚未封存或不能覆盖所需 NBANDS/网格，应先补充并封存波函数，不能静默退回默认路线。其他执行后端需要[接口契约](INTERFACES.md)所述的产物认证集成。

| 输入 | 核对内容 |
|---|---|
| 当前band cache、analysis和decision | `sampling=band_path`，原始PROCAR/EIGENVAL/POSCAR来源哈希一致；轨道和窗口从该band审阅，不先collect另一份mesh作为选择依据 |
| 决策状态 | `proposed_trial`、`next_action=prepare_interface`；`numerical_checks.sampling=band_path`且`mesh_gate_pending=true`。路径计数通过只允许准备接口 |
| SCF与band来源 | 同结构、赝势和密度，静态正常结束、电子收敛，独立EF源自SCF；来源记录存在时核对其输出哈希 |
| 物理设置 | 当前支持静态非磁性半局域scalar或spinor，核对ENCUT、XC、电荷、自旋/SAXIS/MAGMOM、LMAXMIX、LASPH等；不静默切换Hamiltonian |
| 本轮均匀采样输入 | 自动Gamma/Monkhorst网格，或可核对转换的完整显式网格定义；不要求这些点已有波函数或本征值 |
| 历史隔离 | 不读取旧.win、.chk或成功投影/窗口；只使用当前审阅基组和窗口，不继承额外WANNIER90_WIN/LOCPROJ/SCDM构造参数 |

LMAXMIX须在生成SCF密度时满足所用PAW需要；接口阶段改值不能补回密度中已丢失的信息，此设置也不决定基组。跨scalar密度到SOC的自动适配尚需独立实现，不能仅凭CHGCAR可读就宣称物理协议一致。

## 准备目录与单次接口

准备器复制当前结构、POTCAR和SCF CHGCAR，生成本次均匀KPOINTS、INCAR、`wannier90.win.in`审阅内容及`trial_manifest.json`。**准备时不预建`wannier90.win`**：win.in通过INCAR的WANNIER90_WIN传给VASP，由其在本次接口中生成真实几何和k点顺序。prepare不能把预设点序当作已核实的VASP输出。

| 动作 | 职责与边界 |
|---|---|
| `prepare_wannier_interface.py prepare` | 准备一个新目录及输入清单，不执行VASP |
| `dft_workflow.py submit-interface` | 生成/预览提交脚本，不提交计算 |
| `dft_workflow.py submit-interface --execute` | 实际提交接口作业；提交成功不表示作业或输出成功 |
| `dft_workflow.py run-interface` | 在配置要求的计算分配中执行VASP，检查真实返回码、OUTCAR和接口产物，保存执行记录 |
| `prepare_wannier_interface.py validate [--outputs]` | 检查已有输入/输出，不执行VASP，不补出缺失矩阵，也不替代执行记录 |

禁止把`validate --outputs`替换进接口执行命令而继续称为接口NSCF；禁止用校验脚本覆盖`interface_run.json`、伪造`returncode=0`或删除原始失败证据。已执行目录的复核应调用校验入口，不再次调用`run-interface`期待其变为校验模式。只校验的时间/次数与真实VASP接口执行分开记录。

默认`ISTART=0`、`ICHARG=11`、`ISYM=-1`、`NSW=0`、`IBRION=-1`、`LWAVE=.FALSE.`、`LCHARG=.FALSE.`。NBANDS来自当前band的实际维数，NUM_WANN由审阅基组及自旋模式得到。接口选项为：

```text
LWANNIER90 = .TRUE.
LWANNIER90_RUN = .FALSE.
LWRITE_MMN_AMN = .TRUE.
LWRITE_UNK = .FALSE.
NCORE = 1
KPAR = 1
```

移除源NPAR，避免覆盖接口要求的NCORE。执行器在site资源上限内选择不使目标NBANDS因带并行而扩张的MPI ranks并记录实际分配；不就地修改源DFT或site配置。自定义launcher须显式使用`{ntasks}`。大矩阵接口需要多节点内存时，可在专用site profile中设置`"balance_tasks_across_nodes": true`；执行器此时只选择可被节点数整除的NBANDS因子，并写出`--ntasks-per-node`。旧profile若显式给出正整数`ntasks_per_node`，工具会验证`nodes * ntasks_per_node == ntasks`并自动启用相同的均匀布局；不一致的值直接拒绝。非`srun`的多节点launcher还必须显式使用`{ntasks_per_node}`占位符，例如`["mpirun","-np","{ntasks}","-ppn","{ntasks_per_node}"]`。部署者须分别检查rank布局和MPI程序只初始化一次；`hostname`布局烟测不足以证明VASP启动正确。该选项只改变资源布局，技术重试仍须保留原失败记录，不能借此改变基组、窗口、排带或k网格。

win.in和随后生成的实际.win均须显式且唯一地设置`num_iter=0`，新输入固定四个输出开关`write_hr=true`、`write_rmn=true`、`write_tb=true`、`bands_plot=true`，生成hr/r/tb及原生路径能带文件；输出选项同时进入清单和校验契约，不能事后绕过输入身份检查追加。`dis_num_iter`默认且最多10000，可用`--dis-num-iter`指定更小预算；显式设置`dis_conv_tol=1e-8`、`dis_conv_window=3`，2000步未收敛由执行检查额外标记。非零、缺失、重复或追加覆盖的num_iter均拒绝；拟合/收敛重试不能开启最局域化。num_iter=0本身不是严格对称性保证。

原生`kpoint_path`端点与`bands_num_points`从本轮DFT band读取。W90后续段按路径长度缩放采样密度，因此band.dat不能假定与DFT逐行对应；最终使用[正式同点对比](WANNIER-COMPARISON.md)。

manifest保存band证据、SCF密度、采样定义、基组、窗口、预期维数和输入哈希，明确实际mesh检查仍待执行。新目录已存在时拒绝覆盖。准备通过不表示mesh、解纠缠或拟合已通过。

不得手写EIGENVAL解析器、手算并覆盖`num_wann`，或从EIGENVAL/manifest拼装、重建VASP生成的`.win`来通过校验。不得把manifest改成当前输出再重算哈希来自证一致。遇到解析、周期等价坐标或输出选项问题，保留原始文件，在工具副本中修复并记录diff、版本和可复现检查；正式工具根据原始数据重新生成/核验记录。工具未支持的复用操作先报告缺口，不能另写旁路。

```bash
python tools/dft_workflow.py submit-interface \
  --trial /work/material/03_wan_trial/trial_001/interface --site sites/slurm.example.json
# 在已有授权内执行该次接口NSCF：
python tools/dft_workflow.py submit-interface \
  --trial /work/material/03_wan_trial/trial_001/interface --site sites/slurm.example.json --execute
```

默认模式的这个NSCF就是Wannier矩阵生成步骤，不要额外插入mesh/PDOS。明确选择共享模式时，前置的一次波函数NSCF属于跨试算复用的数据准备；此后生成矩阵不再迭代能带。

## 执行后检查与解纠缠

run-interface核对返回码、OUTCAR电子收敛和实际NBANDS，再检查本次EIGENVAL、实际.win的完整k点及矩阵。`interface_run.json`分别保存矩阵一致性、`mesh_gate`和`ready_for_wannier`，不能把文件齐全与窗口可解纠缠混为一谈。

1. 几何、原子顺序、实际均匀点集/权重与输入采样定义一致，保留VASP实际k序。
2. amn/mmn/eig的维数、索引、完整性和有限性正确；eig逐态对应本次EIGENVAL，默认路线不与并不存在的旧mesh本征值比较。
3. 在本次实际mesh逐点检查`Nfrozen <= num_wann <= Nouter`，报告 `Nouter-num_wann` 和计算带顶到已定 `dis_max` 的最小/中位/最大余量；路径预检查不能替代此项。余量或空带不足是补算所需非自洽的信号，不是缩小有目标依据的 `dis` 的理由。
4. 网格窗口不满足时标`window_gate_failed`、`ready_for_wannier=false`，保留证据和成本，停止进入Wannier90。先据证据调整窗口；确认带空间不足才在允许范围内补带。

```bash
python tools/prepare_wannier_interface.py validate \
  --trial-dir /work/material/03_wan_trial/trial_001/interface --outputs
```

只有本次接口及mesh门槛通过才通过[Wannier执行入口](WANNIER-EXECUTION.md)运行独立Wannier90；该入口须再次核对实际`.win`、输入身份和`ready_for_wannier`，不能仅用grep检查`num_iter`后裸跑。矩阵检查不认证投影秩、全BZ精度或拟合成功。

```bash
python tools/wannier_workflow.py prepare \
  --interface-dir /work/material/03_wan_trial/trial_001/interface \
  --out-dir /work/material/03_wan_trial/trial_001/wannier
# 默认submit只生成/预览脚本；已有授权内实际提交时显式加--execute。
python tools/wannier_workflow.py submit \
  --directory /work/material/03_wan_trial/trial_001/wannier \
  --site sites/slurm.example.json --execute
python tools/wannier_workflow.py inspect \
  --directory /work/material/03_wan_trial/trial_001/wannier
```

解纠缠使用独立的新目录，保存`wannier_manifest.json`与`wannier_run.json`。本执行器的只读检查子命令是`inspect`，不是`validate`；`run --directory DIR --site PROFILE`在配置要求的计算分配中实际执行。默认1个rank，明确选择其他`--ntasks`时仍须满足site资源上限，不能把提交成功或inspect成功当作程序已经运行。

后续分别报告四层结果：W90进程正常结束；`.wout`明确记录实际解纠缠收敛；本轮非空chk/hr/r/tb/band.dat/kpt产物齐全并关联输入；按预登记范围完成同一EF、同一k点的拟合验收。`All done`不等于实际解纠缠收敛，达到`dis_num_iter`上限也可能正常退出；前三层通过仍不表示拟合合格。

窗口变化时可在带空间、基组与网格仍匹配的前提下通过正式工具复用矩阵，但须更新决策及本次mesh计数检查，并保存来源关系；不能手改旧manifest或重建`.win`绕过检查。投影变化需要匹配的新amn；带数/网格变化需要相应新矩阵。原始DFT band数、Wannier输入band数和num_wann须区分。本版不支持靠手改exclude_bands规避一致性检查。

带`--band-dir`的旧mesh-dir兼容路径可核对已有mesh及可选WAVECAR，但不能作为默认路线或代替band主证据；真实历史产物不篡改。接口要求参见[VASP官方示例](https://vasp.at/wiki/Si_bandstructure)及[LWANNIER90](https://vasp.at/wiki/LWANNIER90)。

## 已审阅的低能带排除

默认band入口接受decision中的`band_selection`，由[选择工具](MANIFOLD-SELECTION.md)生成并复核。`exclude_bands`使用原始DFT、与k无关的带号，写入INCAR内嵌WANNIER90_WIN；不得手改矩阵或输出win。manifest的`expected.nbands`仍表示VASP总NBANDS，`expected.num_bands_retained`及win的`num_bands`表示排除后矩阵的带数。EIG与完整DFT EIGENVAL比较时使用保存的原始→保留带号映射。

接口实际输出还须检查排除块的占据及采样全局隙。路径上的隔离不能代替接口网格上的核验；失败状态为`exclusion_gate_failed`，即使矩阵格式正常也不得继续解纠缠。这表示当前排除方案需修订，不是材料无法构造模型。`exclusion_recovery`给出实际接口上更短前缀的数值候选（含不排除路线），须与band投影/间隙共同审阅。重新决定exclude和窗口、准备匹配矩阵；原记录保持不变。单轮测试只能报告所需科学修订，不能隐含重跑。旧有mesh兼容入口暂不支持新排除契约；使用默认band入口。没有新增独立mesh计算。
