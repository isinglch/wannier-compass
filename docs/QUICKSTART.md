**[简体中文](QUICKSTART.md) · [English →](QUICKSTART.en.md)**

# WannierCompass：快速上手

**让 Agent 阅读工具包，提供材料和计算环境，即可开始自动 Wannier 构造。**

> 使用 WannierCompass，帮我做 XX 材料的 Wannier TB 哈密顿量。

[下载安装](#mg-install) · [材料与环境](#mg-input) · [开始使用](#mg-start) · [查看结果](#mg-output) · [MCP / CLI](#mg-other)

<a id="mg-install"></a>

## 1. 下载与安装

在准备使用工具包的环境中安装，Python 版本为 3.10+：

```bash
git clone https://github.com/isinglch/wannier-compass.git
cd wannier-compass

python -m venv .venv
source .venv/bin/activate
pip install -e .
```

也可以将仓库地址交给 Agent，让它完成下载与安装。

<a id="mg-input"></a>

## 2. 提供材料与计算环境

把以下信息告诉 Agent：

| 信息 | 提供什么 |
| --- | --- |
| 材料输入 | POSCAR；已有 DFT 计算时，提供 SCF、投影能带及可复用波函数的目录 |
| 计算环境 | VASP、Wannier90、赝势库路径，以及计算资源和任务运行方式 |
| DFT 设置 | 泛函、SOC、磁性、截断能、k 点采样等材料计算参数 |
| 构造目标 | 重点拟合的能量范围、能带或后续研究用途 |
| 运行安排 | 工作目录、可用资源和最多尝试次数 |

已有 DFT 数据时复用现有结果；从结构开始时，由 Agent 准备 SCF 与投影能带计算。

<a id="mg-start"></a>

## 3. 让 Agent 开始构造

让具备文件和命令操作能力的 Agent 阅读 [SKILL.md](../SKILL.md)，然后直接给出任务。例如：

> 请使用 `/path/to/wannier-compass` 中的工具包，为这个材料构造 Wannier TB 哈密顿量。
>
> 先阅读 SKILL.md 和构造概览，再按首次尝试卡分析当前投影能带，选择轨道基组与窗口。复用已有 DFT 数据，需要补充计算时使用我提供的参数和程序路径。
>
> 请在指定工作目录和计算资源内实际运行，最多尝试三轮。每轮保存方案与能带对照，检查收敛和轨道 spread，并根据实际结果继续调整。

后续可以继续告诉 Agent：

> 重点改进费米能附近的拟合，保留当前目标，再尝试一轮。

<a id="mg-output"></a>

## 4. 查看结果

结果保存在任务指定的工作目录中，主要包括：

| 结果 | 内容 |
| --- | --- |
| 基组与窗口方案 | 选择的轨道、Wannier 数目、解纠缠窗口和 frozen 窗口 |
| DFT / Wannier 能带对照 | 同 k 点的能带拟合图与误差记录 |
| 计算与调整记录 | 每轮计算的收敛、spread、修改内容和后续判断 |
| TB 哈密顿量 | `wannier90_hr.dat` 等 Wannier 输出文件 |

<a id="mg-other"></a>

## 其他入口：MCP / CLI

<details>
<summary>通过 MCP 连接 Agent</summary>

在客户端的 MCP 配置中添加以下内容，填写工具包和工作目录的实际路径：

```json
{
  "mcpServers": {
    "wannier-compass": {
      "command": "/path/to/wannier-compass/.venv/bin/wannier-compass",
      "env": {
        "WAN_WORKSPACE_ROOT": "/path/to/workspace",
        "WAN_ALLOW_SUBMIT": "1"
      }
    }
  }
}
```

连接后，让 Agent 调用 `workflow_guide`，读取构造与运行说明。提交计算时使用 `execute=true`。

</details>

<details>
<summary>直接调用 CLI 工具</summary>

安装后可通过 `wannier-compass-run` 调用工具。具体命令和输入格式见 [运行卡](../wannier_mcp/workflow/docs/RUNTIME.md)与[输入输出接口](../wannier_mcp/workflow/docs/INTERFACES.md)。

</details>

[← 项目首页](https://github.com/isinglch/wannier-compass) · [DFT 输入准备](../wannier_mcp/workflow/docs/DFT-PREPARATION.md) · [构造方法](../wannier_mcp/workflow/docs/CONSTRUCTION-OVERVIEW.md)
