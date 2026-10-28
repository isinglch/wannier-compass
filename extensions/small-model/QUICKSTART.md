**[简体中文](QUICKSTART.md) · [English →](QUICKSTART.en.md)**

# 小模型工作流：快速上手

**以本地小模型为例(Qwen 3.5-122b @ 4x RTX 4090)，介绍细分角色、采用结构化方案与交接。**

我们希望让本地可部署的小模型也能完成自动 Wannier 构造。为适应这类模型的能力，将复杂任务拆分为职责明确的角色，采用结构化方案与交接，减少每一步需要同时处理的问题。

[安装](#sg-install) · [配置](#sg-config) · [材料输入](#sg-input) · [启动](#sg-run) · [查看结果](#sg-output) · [示例图片](#sg-examples)

使用环境：Python 3.10+、OpenAI 兼容模型接口、VASP、Wannier90。

<a id="sg-install"></a>

## 1. 下载与安装

```bash
git clone https://github.com/isinglch/wannier-compass.git
cd wannier-compass/extensions/small-model

python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python agent --help
```

后续命令都在 `extensions/small-model` 目录、同一个 Python 环境中执行。

<a id="sg-config"></a>

## 2. 配置模型与计算程序

复制配置模板，然后编辑 `agent.local.json`：

```bash
cp agent.json agent.local.json
```

| 配置项 | 填写内容 |
| --- | --- |
| `models.default.base_url` | 模型 API 地址，例如 `http://127.0.0.1:8000/v1` |
| `models.default.model` | 模型服务提供的模型 ID |
| `execution.vasp` | VASP 命令数组，例如 `["/path/to/vasp_std"]` |
| `execution.wannier90` | Wannier90 命令数组，例如 `["/path/to/wannier90.x"]` |
| `execution.mesh_grid` | 接口计算网格，例如二维材料使用 `[8, 8, 1]` |
| `max_trials` | 最多尝试次数，模板为 `3` |

SOC 材料使用对应的 `vasp_ncl` 命令；MPI 启动参数也填写在命令数组中。

<details>
<summary>模型认证、上下文与其他配置</summary>

模型接口需要密钥时，复制 `.env.example` 为 `.env`，填写 `MODEL_API_KEY`。

```bash
cp .env.example .env
```

`models.default.context_tokens` 和 `max_tokens` 按模型服务设置。程序自动读取完整的 `agent.local.json`；也可以用 `--config` 指定其他配置文件。

</details>

<a id="sg-input"></a>

## 3. 准备材料目录

将已完成的 SCF 和投影能带数据放在同一个材料目录中：

```text
CASE/
├── 01_scf/
│   └── INCAR  POSCAR  POTCAR  CHGCAR  OUTCAR
└── 02_band/
    └── INCAR  POSCAR  POTCAR  KPOINTS  EIGENVAL  PROCAR  charge.npz
```

`charge.npz` 是由当前投影能带数据生成的缓存。

<details>
<summary>首次使用：生成 charge.npz</summary>

```bash
python agent cache --poscar /ABS/CASE/02_band/POSCAR --eigenval /ABS/CASE/02_band/EIGENVAL --procar /ABS/CASE/02_band/PROCAR --kpoints /ABS/CASE/02_band/KPOINTS --scf-outcar /ABS/CASE/01_scf/OUTCAR --sampling band_path --out /ABS/CASE/02_band/charge.npz
```

将 `/ABS/CASE` 换成材料目录的绝对路径。

</details>

<a id="sg-run"></a>

## 4. 检查并启动

```bash
python agent check /ABS/CASE
python agent run /ABS/CASE
```

`check` 检查本地环境和程序配置；`run` 启动工作流，依次完成方案制定、审阅、计算和结果评估。

<details>
<summary>先查看方案，或指定结果目录</summary>

只生成并审阅方案：

```bash
python agent run /ABS/CASE --review-only
```

指定结果保存位置：

```bash
python agent run /ABS/CASE --runs-root /ABS/OUTPUT
```

</details>

<a id="sg-output"></a>

## 5. 查看过程与结果

查看运行过程：

```bash
python agent view
```

结果默认保存在 `runs/<本次运行编号>/`：

| 文件或目录 | 内容 |
| --- | --- |
| `final-report.json` | 本次运行总结与角色结论 |
| `plans/` | 每轮方案和审阅记录 |
| `trials/` | 各轮计算输入、输出和拟合结果 |
| `plots/index.html` | 能带与方案图片索引 |

<a id="sg-examples"></a>

## 示例图片

![DFT 与 Wannier 的同 k 能带比较](docs/assets/band-comparison.png)

运行结果中的 DFT / Wannier 能带对照。

[← WannierCompass 项目首页](../../README.md)
