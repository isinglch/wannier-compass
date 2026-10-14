# Cu 图示说明

[项目主页](../README.md) · [English](CU-WORKFLOW-ILLUSTRATION.en.md)

以新手容易遇到拟合困难的面心立方铜（fcc Cu）为例，展示 AI Agent 如何使用 WannierCompass。测试分别使用 **GPT-6.0sol（mid）@Codex**、**GLM-5.3flash（本地部署）@OpenCode** 和 **DeepSeek-v4.1flash（low）@DeepSeek Harness**。首次输入只提供工具包路径、POSCAR、VASP 计算所需参数与相关程序及执行路径，未额外指定基组、窗口或重试策略，仅要求：

> 完成 DFT 计算，并做好 fcc-Cu 的 Wannier。

![Cu 工作流示意](assets/cu-ai-workflow.zh-CN.png)

图中 `d+s` 和 `d+s+p` 是两类尝试。某次较小基组的结果在相关导带上存在偏差时，加入 `p` 可以成为下一次待验证的假设；另一种目标或证据可能支持保留较小基组。原子投影中出现某种成分，不自动意味着必须增加该投影基组。需要同时检查目标、窗口和实际拟合。

角色气泡概括决策思路，不作为逐字对话记录。AI 模型名称、推理档位及本地部署标签采用作者图中的标注。勾号表示该次运行不再进行下一轮计算；用户可以根据所需精度和拟合范围要求 AI 进一步调整。它不认证任意高能空带、完整 BZ、对称性或未声明的物理用途。

## frozen 与 dis 窗口的放大对照

左图放大 frozen 窗口附近的能带，右图显示 dis（outer）窗口及其周边能带。灰色实线为 DFT 能带，红色虚线为 Wannier 插值能带；橙色和绿色点线分别标出 frozen 与 dis 窗口的边界。

![Cu 的 frozen 与 dis 窗口能带对照](assets/cu-window-zoom.png)
