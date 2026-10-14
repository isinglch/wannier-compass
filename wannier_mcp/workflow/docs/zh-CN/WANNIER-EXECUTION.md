> 中文阅读译本；执行主版本： [English](../WANNIER-EXECUTION.md)。
> 对应英文来源 SHA256：`5fc2992684f9f262bd18610b8f496f115e7229b1f466eaa68f6d1d3619db1594`。

# 独立 Wannier90 解纠缠执行

执行阶段以[initial与real_first_try](DECISION-STAGES.md)为准：独立propose只作草稿；新执行必须绑定封存的real_first_try/retry。initial不直接执行，旧结果仍可只读核查。

`tools/wannier_workflow.py` 负责已通过接口校验后的原生 Wannier90 执行。
它不会选择轨道、改窗口、补跑 VASP，也不会打开最局域化：输入必须显式
`num_iter = 0`。拟合精度验收与作图是后续独立步骤。

## 准备

```bash
python tools/wannier_workflow.py prepare \
  --interface-dir /absolute/interface-trial \
  --out-dir /absolute/wannier-run-001
```

来源须通过 `prepare_wannier_interface.validate(outputs=True)`，且
`interface_files_consistent=true`、`ready_for_wannier=true`。准备命令复制
`wannier90.win/.amn/.mmn/.eig` 到全新目录，不依赖来源 WAVECAR。
几何、实际接口 k 点顺序、投影与能窗保持原值；预算和收敛控制按下述当前协议显式设置并记录前后值。

同时必须提供官方 `dft_workflow.py run-interface` 产生的成功 `interface_run.json`：
返回码为 0，manifest ID、成功状态和当前输出哈希一致。入口还独立读取当前
OUTCAR，核对正常结束、电子 EDIFF 收敛，以及 NSW=0、ICHARG=11、实际 NBANDS。
修改记录里的成功标志或重填哈希不能代替这些事实检查。仅有完整矩阵、缺少官方
执行证据的外部接口当前不受支持，没有跳过门禁的参数。

`write_hr=true`、`write_rmn=true`、`write_tb=true`、`bands_plot=true` 均被显式写入，
兼容已完成接口尚未请求这些输出的情况。`kpoint_path` 从来源 manifest 指向的
band/KPOINTS 与实际 band/EIGENVAL 通过现有路径适配器生成；已有 fit_path.json
也必须与之吻合。路径端点保持实际分数坐标，标签被稳定地转换为 W90 可用名称。
旧接口没有 band 来源时须明确提供 `--band-dir /absolute/band`，不能猜路径。
来源结构须与已验证接口一致；当前 band 路线还核验原 band 文件哈希。
已存在且一致的 kpoint_path 原样保留，不能以输出文件回写科学几何来绕过验证。

`bands_num_points` 以原 DFT 首段采样数为基准显式设置；若按路径长度缩放会让某段
得到零个插值间隔，则只提高绘图预算，保证每个原有段至少一个间隔。这是输出采样
设置，不改变 DFT、Wannier 基组或能窗。新准备默认且最多
`dis_num_iter=10000`，允许用 `--dis-num-iter N` 指定更小预算；同时显式写入
`dis_conv_tol=1e-8`、`dis_conv_window=3`，始终 `num_iter=0`。
复用旧接口时同样应用这些设置，记录前后值，不修改原接口文件或矩阵。
旧封存结果仍可检查；要用新执行入口提交旧参数输入，须重新 prepare 到新目录。

`wannier_manifest.json` 保存来源路径、来源接口 manifest ID、来源/当前输入
SHA-256、原始设置与几何块、全部允许修改的前后值、准备时间。已存在目录拒绝覆盖。
来源记录还绑定 `interface_run.json` 和 OUTCAR 的哈希及交叉检查摘要。

## 提交与运行

```bash
# 仅输出可审阅的 Slurm 脚本，不调用 sbatch。
python tools/wannier_workflow.py submit \
  --directory /absolute/wannier-run-001 --site sites/slurm.example.json \
  --ntasks 36

# 明确提交上述执行任务。
python tools/wannier_workflow.py submit \
  --directory /absolute/wannier-run-001 --site sites/slurm.example.json \
  --ntasks 36 --execute

# 仅用于已有 Slurm allocation 中；提交脚本调用的就是此入口。
python tools/wannier_workflow.py run \
  --directory /absolute/wannier-run-001 --site sites/slurm.example.json \
  --ntasks 36
```

默认 `--ntasks=1`。多 rank 执行应使用确认支持 MPI 的 Wannier90 build；显式
rank 数不能超过 site.ntasks，实际 SLURM_NTASKS 不能少于请求值。
实际执行参数为站点 launcher 加 `site.wannier90 wannier90`，通过站点
`environment_script` 和 `environment_args` 初始化运行环境。
site 中的 partition/account/time/exclude 会进入提交脚本。
`run` 不支持 local 模式，缺少 Slurm allocation 时拒绝执行，避免在登录节点计算。

执行前再次检查输入哈希、显式 `num_iter=0`、四个输出开关、绘图路径及独立的解纠缠预算。
已存在执行日志、wout、chk、HR 或执行记录时拒绝重跑；重试应重新 prepare 到新目录。
该入口直接调用 Wannier90，不调用 VASP、接口 validator 的执行子命令或手写局域化驱动。

## 状态、费用记录与检查

```bash
python tools/wannier_workflow.py inspect --directory /absolute/wannier-run-001
```

`wannier_run.json` 包含实际执行 argv（含环境初始化 wrapper）、Slurm job ID、
site 哈希、请求及实际可用 rank 数、输入/输出哈希、进程返回码、开始/结束时间和
`wall_seconds`。后者计执行命令墙钟时间，包含环境初始化和 MPI 启动，
不包含队列等待、输入复制、后续哈希检查或画图；它不是 W90 主程序内部计时。
仅明确做成本评测时，才由外部客户端账本提供 token 统计；正常执行不要求它，本工具不推测 token 用量。
`inspect` 会重新核验准备输入和已记录输出哈希。

解纠缠收敛按 Wannier90 对规范不变 spread 的相邻迭代相对变化判断，
连续三步小于 `1e-8`；这不是能带拟合误差容差。达到第 2000 步仍未收敛时，
结果额外记录 `not_converged_by_2000=true` 与 `slow_disentanglement`，可继续至
10000 步；后来收敛也保留慢收敛标记。该标记由完整日志检查产生，不是后台通知。
若日志不完整且尚未到 2000 步，该字段为 null，不能当作已达标。
10000 步仍未收敛则保留 provisional 状态，优先复核窗口、基组和数值行为，
不自动延长预算，也不直接据此认定材料无法构造 Wannier。
旧日志按其当时输入判据报告；不能把旧 `1e-10` 的未收敛改写成新判据的成功运行。

| status | 意义 | 后续使用 |
| --- | --- | --- |
| `completed_converged` | 明确解纠缠收敛标志、实际 DIS 迭代记录、正常退出及全部必需产物同时成立 | 可继续拟合验证；尚未通过科学验收 |
| `completed_iteration_limit` | 正常结束，但实际 DIS 到达预算并明确报告未收敛 | 仅可标注为 provisional 的拟合检查；不能称收敛或 accepted |
| `completed_no_disentanglement_required` | num_bands=num_wann，未执行解纠缠，正常输出完成 | 可继续拟合验证；不称解纠缠已收敛 |
| `execution_failed` | 程序失败、输出缺失、迭代记录不一致或无法确认解纠缠状态 | 不进入可用拟合结果集合，检查 problems 与日志 |

所有状态都保留 `accepted=false` 和 `fit_pass=null`。达到迭代上限时会明确记录
`provisional_fit_ready=true`、`fit_status=provisional_unconverged`，不会自动提高预算、
改变窗口或启用最局域化。`run/inspect` 对完整的 provisional 产物返回退出码 0，
对 `execution_failed` 返回 2；自动调用者必须读取 JSON 状态，不能把退出码 0 当成收敛。

必需的非空产物是 `wannier90.chk`、`wannier90_hr.dat`、`wannier90_r.dat`、
`wannier90_tb.dat`、`wannier90_band.dat`、`wannier90_band.kpt`。文件齐全只表示
执行产物完整，不表示矩阵质量或拟合误差验收通过；结束后补入外部产物会导致记录
清单/哈希不一致，不能让失败记录自动翻成成功。

原生 `bands_plot` 按各段长度和连通性生成自身点序；即使沿同一条物理路径，
也不保证与原 DFT 采样点数、重复端点和顺序一致。因此原生能带用于查看输出，
精确逐点误差比较仍使用 postw90/geninterp 在原 DFT 实际 k 点求值，之后通过
`plot_fit_review.py` 的路径对齐门禁，不能直接把原生点序当成 DFT 对应点。

解析依据是 [Wannier90 3.1.0 disentangle 源码](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/disentangle.F90)
中的逐次 DIS 记录和明确收敛/上限提示；正常结束标志来自
[Wannier90 主程序](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/wannier_prog.F90)。
`All done` 或返回码 0 只能证明正常结束的一部分，不能代替解纠缠收敛证据。

## 墙时中断后的技术性输出恢复

若来源 W90 已完成真实 DIS，并已写出稳定的 **postwann** `wannier90.chk`，但在
`write_hr/write_rmn/write_tb/bands_plot` 或最终哈希记录阶段被墙时中断，可在
canonical trial 的新目录 `technical/restart_NNN/wannier/` 准备一次只恢复输出的作业：

```bash
python tools/wannier_workflow.py prepare-output-recovery \
  --source-dir TRIAL/wannier \
  --out-dir TRIAL/technical/restart_001/wannier \
  --source-job-id 123456

python tools/wannier_workflow.py submit-output-recovery \
  --directory TRIAL/technical/restart_001/wannier \
  --site sites/slurm-output-recovery.example.json --ntasks 36 --execute
```

`source-job-id` 必须是原 Slurm 作业号；若已有部分 `wannier_run.json`，两者必须一致。
提交脚本固定使用 `#SBATCH --dependency=afterany:<source-job-id>`。依赖任务真正启动时先
查询来源作业：来源仍在队列中就拒绝读取；若常规 `wannier_workflow.inspect` 已能完整认证
来源目录，则写入 `no_op_source_already_complete` 记录并退出，不重复执行。

恢复 manifest/record 均显式记录 `technical_output_recovery=true`、
`scientific_proposal_changed=false`、来源路径、作业号、来源 manifest/WOUT/chk/eig 哈希、
inode 与 materialization 方法。目标目录必须全新；来源目录不写入。`.chk/.eig` 优先硬链接，
跨文件系统时回退为复制；`.win` 只增加唯一的 `restart=plot`。任何窗口、基组、几何、
`num_iter=0`、DIS 控制或四个输出开关变化都会拒绝。目标目录不会复制 `.amn/.mmn`：
Wannier90 3.x 的 `restart=plot` 从 `.chk` 读取 U/M 矩阵，`write_hr/write_tb/bands_plot`
另需 `.eig`。这一最小集合依据
[Wannier90 3.1.0 主程序](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/wannier_prog.F90)
和 [plot 路径](https://github.com/wannier-developers/wannier90/blob/v3.1.0/src/plot.F90)。

来源 WOUT 必须给出可解析的真实 DIS 终态，且在 `Final State` 后记录完成的 chk 写入；
恢复 WOUT 则必须只有 `Restarting Wannier90 from plotting routines` 与末尾唯一 `All done`，
不得出现新 DIS 迭代。`inspect` 自动识别恢复 manifest，从来源 WOUT 继承收敛上下文，
同时核验恢复 WOUT 和 chk/hr/r/tb/band 六项产物哈希。来源达到迭代上限时，恢复后仍是
`completed_output_recovery_iteration_limit`、`provisional_unconverged`、`accepted=false`。
恢复不能把技术文件补齐解释成科学收敛或拟合验收。

`sites/slurm-output-recovery.example.json` 是专用恢复模板；实际时间上限和节点排除须按站点填写；
它只是资源配置，不改变上述门禁，也不代表恢复通常需要 8 小时。

## 仅改窗口时复用已有矩阵

已有官方执行记录、相同基组和投影顺序、相同原始band缓存、相同EF/自旋/维数时，可以给`prepare`同时提供新的`--cache`、`--analysis`、`--decision`。这三个参数必须成组出现；新的decision须通过当前band审阅，工具还独立使用旧接口的**实际EIGENVAL网格**重算新窗口的态数和顶部余量。这里只复用矩阵，不把band预检查误报为网格检查。

```bash
python tools/wannier_workflow.py prepare --interface-dir OLD_INTERFACE \
  --out-dir NEW_TRIAL/wannier --cache CASE/02_band/charge.npz \
  --analysis NEW_TRIAL/records/selection/analysis.json \
  --decision NEW_TRIAL/records/selection/decision.json
```

只有四个窗口标量允许按新记录改变，原始矩阵、几何、k序、投影和旧记录不变。旧VASP真实执行失败、未收敛、矩阵改变、换基组或换缓存时不能这样复用；换基组需要对应的新AMN。`num_iter=0`及四个输出开关仍强制保留。prepare只是准备新目录，不提交或执行W90；只选择实验不要调用它。

窗口复用必须保持`exclude_bands`及原始→保留矩阵带号映射不变。改变排除块需要重新生成匹配接口，不能仅改win后继续使用旧AMN/MMN。
