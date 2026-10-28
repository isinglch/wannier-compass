# pythonagent-scheduler 主项目使用说明

本包来自主项目 `codex/scheduler`，版本 `0.2.0.dev11`，基线提交 `d94d7a0`。不包含 `codex/tool-evidence-v2` 实验分支的工具、科学经验重整及最新压缩改造，也不包含观察台实验分支的界面改造。

这是独立运行的 Python 项目，不需要父项目、MCP 服务或旧项目目录。包内保留主版本自带的科学指导正文、角色规则、Schema、统计工具、固定绘图程序和观察台。

## 1. 包内内容与首次准备

压缩包包含源码、说明、通用配置、空密钥模板、Schema 和离线测试。没有打包 `.git`、`.env`、私有配置、测试材料、POTCAR、运行记录、会话、模型文件、服务器软件、虚拟环境或历史验证产物。

`agent.json` 中模型地址和模型名称为空；VASP/Wannier90 命令是明确的待配置占位符。环境模块、私有软件路径、代理地址和默认材料路径均未预存。不能直接使用原服务器配置运行。程序会继承启动进程的环境；这不表示压缩包内保存了该环境。

首次使用顺序：**安装依赖 → 配置模型与软件 → 准备材料证据 → check → run**。

## 2. Python 环境与安装

需要 Python **3.10 或更新版本**。依赖为 NumPy、SciPy、jsonschema、Matplotlib，由 `pyproject.toml` 声明。VASP、MPI 和 Wannier90 需要在目标机器另行安装；VASP 应支持 Wannier90 接口。不要将使用相同 `pythonagent` 包名的其他版本安装到同一个环境。

在解压目录执行以下命令；其中 `python3` 必须为 3.10+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python agent --help
```

安装依赖需要可用的软件源；本压缩包不是包含所有依赖的离线安装包。也可以使用已有的、满足依赖的 Python 环境。源码启动命令 `python agent ...` 总是使用本目录的源码；`./agent ...` 要求 `python3` 指向合适的环境。

安装后的等价控制台命令为 `pythonagent-scheduler`。保留源码目录及其 `agent.json`；从其他目录使用控制台命令时建议显式指定 `--config /ABS/PROJECT/agent.json`。

## 3. 配置文件

统一配置入口为根目录 `agent.json`。可以直接编辑；或者复制为 `agent.local.json`，保留公开模板。优先级为：

1. 命令行显式 `--config`。
2. 项目根目录 `agent.local.json`。
3. 项目根目录 `agent.json`。

`agent.local.json` 是完整替代文件，**不是增量补丁**。配置中的相对路径以配置文件目录为准；命令行提供的材料和输出路径按当前工作目录解释。

### 模型

将 `models.default.base_url` 填为服务商提供的 OpenAI 兼容 API 基地址，将 `models.default.model` 填为该服务实际公布的模型 ID。两者在分发配置中均为空，必须自行填写。不要把网页聊天地址当作 API 基地址。

公共 `llm` 与所选 `models` 项递归合并；数组和标量整体替换。默认模型配置名为 `default`，可新增其他配置名，通过 `--model NAME` 选择。

关键字段：

| 配置项 | 含义 |
| --- | --- |
| `llm.api` | 模板使用 `chat_completions`，由本地维护角色历史；Responses 只在服务实际支持时配置 |
| `models.default.max_tokens` | 单次输出上限，模板为 65536；必须按服务和所用模式调整 |
| `models.default.context_tokens` | 总上下文容量，模板为 131072；这是示例容量，不代表程序已检测服务上限 |
| `models.default.proxy_mode` | 模板为 `direct`，不使用环境代理；需要代理时改为 `environment` |
| `llm.request_extra.response_format` | 模板请求 `json_object`；不是完整 Schema 的服务端强约束，模型仍需遵守工具及动作协议 |
| `llm.stream` | 开启流式日志；完整响应收取、解析后才执行工具 |
| `llm.compression` | 由独立模型请求压缩角色历史，会产生额外 token 消耗 |

输入、输出及安全余量共同占用容量：128k 总上下文并不等于可以提交 128k 输入后再生成 64k 输出。服务可能没有计数接口，`check` 也不会验证真实上下文容量。服务报告的 usage 与估计值需区分。

分发配置没有预置任何厂商的思考开关。若服务明确支持，可在所选模型项下添加 `chat_template_kwargs: {"enable_thinking": false}` 或 `true`；这不是所有兼容 API 通用支持的参数。推理程度参数同样依照服务契约自行配置，不应盲目沿用其他模型设置。

### 密钥与环境变量

```bash
cp .env.example .env
```

将密钥填入 `.env` 的 `MODEL_API_KEY=` 后面。也可在启动前导出同名环境变量；已经存在的环境变量优先。配置通过 `api_key_env` 引用变量名，密钥不要写进模型配置。

无需认证的服务允许不提供密钥。需要缺少密钥时立即终止，可配置 `runtime.required_env` 为 `["MODEL_API_KEY"]`。`.env` 按字面值读取，不执行 shell、变量替换或命令替换；含空格的值应加引号。不支持多行值和行尾注释。

`runtime.environment` 已保留通用线程设置：`OPENBLAS_NUM_THREADS`、`OMP_NUM_THREADS`、`MKL_NUM_THREADS`、`NUMEXPR_NUM_THREADS` 均为 `1`，避免每个 MPI 进程额外启动大量线程。这些设置作用于程序及其子进程，不修改 `.bashrc` 或调用者 shell。

没有指定 `runtime.python` 时使用当前解释器。也可自行填写目标环境的 Python 路径。按顺序加载：配置 → 环境模块 → 可选 `.env` → 显式环境变量及软件路径 → 所选 Python 重新启动 → 科学库及工作流。

### VASP、Wannier90 与计算网格

将 `execution.vasp` 与 `execution.wannier90` 中的占位符替换为实际命令数组。例如以下只是命令结构示例，需按照目标机器实际软件、MPI 和材料设置填写：

```json
{
  "vasp": ["mpirun", "-np", "8", "vasp_ncl"],
  "wannier90": ["mpirun", "-np", "8", "wannier90.x"]
}
```

SOC/非共线材料需要适用的 `vasp_ncl`，普通 scalar 输入通常使用 `vasp_std`。不要对所有材料机械套用同一二进制或网格。MPI 的 8 指 8 个进程，并不自动进行 CPU 分配或核绑定；资源和 MPI 兼容性由操作者保障。

命令数组以 `shell=False` 执行，不支持在元素中使用管道、`&&` 或 `module load` 等 shell 命令。程序不会自动提交 Slurm；直接使用 MPI 命令就是在当前主机/已有分配环境执行。

`execution.mesh_grid` 是实际接口计算的均匀 Gamma 网格。模板 `[8,8,8]` 仅为格式示例，必须按材料配置；程序不会替操作者进行网格收敛判断。`timeout_seconds=3600` 是每条外部命令的超时，而非整个工作流的总时限。模型请求超时保留主版本的 600 秒设置。

有 environment modules 的机器可自行配置 `runtime.modules.init/load/purge`；分发包的 `load` 为空，不会加载任何旧服务器模块。无 module 时可通过 `runtime.prepend_path`、`runtime.prepend_library_path` 提供软件和动态库路径，或者填写绝对可执行文件路径。

## 4. 材料输入

本项目从**已经完成的 SCF 和能带计算**开始，原生执行目录为：

```text
CASE/
  01_scf/
    INCAR POSCAR POTCAR CHGCAR OUTCAR
  02_band/
    INCAR POSCAR POTCAR KPOINTS EIGENVAL PROCAR charge.npz
```

SCF 与能带输入必须属于同一体系和一致物理设置，SCF 应正常结束且提供正确 EF。原始文件与缓存有来源哈希绑定，缓存建立后不要直接替换其来源文件。

没有 `charge.npz` 时，用本项目转换器生成：

```bash
python agent cache \
  --poscar /ABS/CASE/02_band/POSCAR \
  --eigenval /ABS/CASE/02_band/EIGENVAL \
  --procar /ABS/CASE/02_band/PROCAR \
  --kpoints /ABS/CASE/02_band/KPOINTS \
  --scf-outcar /ABS/CASE/01_scf/OUTCAR \
  --sampling band_path \
  --out /ABS/CASE/02_band/charge.npz

python agent inspect --case-dir /ABS/CASE
```

缓存转换不会覆盖已有输出；需要重建时使用新的目标文件/材料副本。当前支持单能谱 scalar 或 spinor 及完整角动量壳层证据；不支持共线 `ISPIN=2` 双谱。路径缓存使用可核验的 reciprocal Line-mode KPOINTS。

程序没有自动生成新 SCF、重建能带或扩展源缓存的通用能力。角色提出此类需求不代表程序已经执行；所需新数据必须由操作者准备后开启新工作流。

## 5. 日常启动

配置完成、材料准备好之后：

```bash
python agent check /ABS/CASE
python agent run /ABS/CASE
```

模板保留主版本 `execute=true`，因此 `run` 会在 reviewer 批准后运行所配置的软件。需要仅完成方案起草与审查时：

```bash
python agent run /ABS/CASE --review-only
```

设好 `paths.case_dir` 后可简化为 `python agent run`。其他常用参数：

```bash
python agent run /ABS/CASE --config /ABS/agent.local.json --model default
python agent run /ABS/CASE --runs-root /ABS/OUTPUT
python agent view
```

`check` 只检查本地依赖可导入、配置的程序能找到、材料目录存在与必要环境变量。它不检查全部材料文件是否完备，不测试 API 地址、认证有效性、模型名称、上下文上限或 MPI 实际运行，也不会请求模型或执行 VASP/Wannier90。`ok=true` 不能当作全流程通过。

四个科学角色依次负责规划、审查、评估和迭代策略；第五个 scheduler 只处理操作调度。planner 保存完整版本方案，程序检查格式、来源、算术和实际输入约束；reviewer 批准准确版本后才能执行。窗口与基组等科学选择由角色决定。

本版本只进行解纠缠，方案中 `solver.num_iter` 必须为 `0`。`max_trials=3` 为科学实验预算，`max_steps=32` 为角色介入预算；没有工具查询次数预算。达到限制、数据阻塞或模型结束并不等价于科学结果合格。

每次 `run` 都创建全新工作流和角色会话，即使材料及输出目录相同也不读取旧会话。主版本仍存在上下文占用、压缩频率及模型反复修稿的实际限制；实验分支的改进没有合入本包。

## 6. 观察台与绘图

另开一个终端，使用同样的 Python 环境及配置：

```bash
python agent view --runs-root /ABS/OUTPUT --port 8765
```

本机访问 `http://127.0.0.1:8765`。观察台只监听 loopback，不自动公开到外网。远程机器可在本地电脑建立 SSH 转发：

```bash
ssh -N -L 8765:127.0.0.1:8765 USER@SERVER
```

启动 `view` 不会启动计算。观察台与工作流是独立进程，但功能代码属于本包。`run` 与 `view` 的 `runs_root` 必须指向同一目录。停止观察台不会停止后台计算。

绘图由固定程序生成：DFT 图在 `plots/dft/`，方案图在 `plans/<id>/plots/`，有完整拟合结果的实验图在 `trials/<id>/plots/`。索引为 `RUN/plots/index.html`。只有产生相应有效结果时才有拟合/残差图。模型接口当前发送文本，图片生成不意味着模型已经视觉读取图片。

## 7. 结果与排错

默认结果保存在配置目录下 `runs/<timestamp-id>/`：

| 文件/目录 | 用途 |
| --- | --- |
| `final-report.json` | 结束原因、角色结论、实验次数及耗时 |
| `journal.jsonl`、`events.jsonl` | 工具、模型、压缩、计算等事件 |
| `calls/`、`sessions/` | 本轮模型请求响应与角色会话审计 |
| `plans/`、`handoffs/` | 版本方案、校验、审批和角色交接 |
| `trials/` | 实际输入输出、执行事实、失败信息与拟合指标 |
| `observations/` | 超长工具结果的存档 |
| `plots/` | 绘图及索引 |

成功结束代码为 0 的情况包括模型结束、模型停止或仅审查完成；其他结束通常返回 2。退出代码 0 也不代表物理验收通过。读取 `stop`、覆盖、误差和解纠缠状态；达到迭代上限的拟合是 provisional。不要把正常进程退出、`All done` 或图片生成当作科学收敛。

常见情况：

- **依赖或软件找不到**：先确认所用 Python，再检查软件 argv、PATH、模块及动态库设置。
- **HTTP 认证、连接或参数错误**：核对用户填写的 API 地址、模型 ID、密钥、代理及服务支持的参数。`check` 不会提前发现这些远程问题。
- **缓存来源不匹配**：核对 SCF/能带原始文件；不要绕过来源检查或用旧材料缓存替代。
- **`outer_dimension` / `frozen_dimension`**：实际输入的态数与批准方案不满足维度约束，需按证据重新审查方案；不是一律增加 NBANDS 就能解决。
- **`model_data_blocked`**：模型认为关键证据缺失；核对其请求及当前适配器能力，不等于程序已经补算。
- **压缩或输出长度问题**：检查服务真实容量、输出预算、usage 与日志。压缩会调用模型，不能保证摘要无损或所有模型都可靠遵守协议。

Ctrl+C 可以中断前台工作流。对于脱离终端的进程，应根据该工作流进程及其子进程实施停止，不要用全局名称匹配终止其他任务；观察台没有通用停止按钮。

## 8. 打包清单与开发检查

`PACKAGE-MANIFEST.json` 记录主版本提交、所含文件 SHA256 和分发调整。除为去除默认模型地址而调整 `config.py` 的示例配置外，运行源码保留主项目原文件。科学指导正文保持原文件哈希。说明中的泛化示例不是原服务器设置。

在依赖齐全的环境运行离线回归：

```bash
PYTHONPATH=src:tests OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  python -m unittest discover -s tests -v
```

这些测试不代表某材料的真实 VASP/Wannier90 计算或模型科学能力已经通过。依赖、商业软件、模型服务及其费用由目标环境提供。后续如重新分享项目，请再次排除新产生的 `.env`、本地配置、材料数据、运行目录和会话。
