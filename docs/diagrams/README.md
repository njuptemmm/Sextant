# 答辩图表

本目录是赛道 B 提交材料里的图表源文件：**SVG 可编辑，PNG 可直接放进 PPT**，两种格式内容一致。

## 1. 图表清单

| # | 图 | 回答什么问题 | 主要数据来源 |
|---|---|---|---|
| 01 | [系统架构](01-architecture.svg) · [PNG](png/01-architecture.png) | 系统由哪些部分组成，两个入口如何共用一套内核，产物落在哪 | `docs/architecture.md` |
| 02 | [Agent 执行流程](02-agent-flow.svg) · [PNG](png/02-agent-flow.png) | 一次运行被拆成哪些阶段，每段的产物与失败边界是什么 | `docs/agent-flow.md` |
| 03 | [报告任务时序](03-report-sequence.svg) · [PNG](png/03-report-sequence.png) | 报告为什么是独立任务，冲突与失败如何返回 | `docs/architecture.md` §3.3、`docs/evidence.md` |
| 04 | [运行状态机](04-state-machine.svg) · [PNG](png/04-state-machine.png) | 检索与报告各有哪些状态，重跑和重新生成如何流转 | `api/jobs.py`、`docs/error-handling.md` |
| 05 | [异常处理](05-error-handling.svg) · [PNG](png/05-error-handling.png) | 六类失败域分别怎么处理，用户看到什么 | `docs/error-handling.md` |
| 06 | [可观测性](06-observability.svg) · [PNG](png/06-observability.png) | 事件如何从 RunLogger 流向日志、SSE 和汇总 | `docs/observability.md`、真实 JSONL |
| 07 | [成本与性能](07-cost-performance.svg) · [PNG](png/07-cost-performance.png) | 时间花在哪、一次运行多少钱、占多少资源 | `docs/cost-and-performance.md`、`run_summary.json` |
| 08 | [场景价值](08-value-comparison.svg) · [PNG](png/08-value-comparison.png) | 人工流程与 Sextant 的差异，量化收益是多少 | `docs/scenario-and-value.md` |
| 09 | [部署拓扑](09-deployment-topology.svg) · [PNG](png/09-deployment-topology.png) | 一条命令启动后有哪些进程、共享什么、访问哪些外部服务 | `docker-compose.yml`、`docs/architecture.md` §5 |

## 2. 与评分项的对应

| 评分项 | 分值 | 建议使用的图 | 配套文字 |
|---|---:|---|---|
| 场景价值与痛点判断 | 20 | 08 | [scenario-and-value.md](../scenario-and-value.md) |
| 系统架构与技术选型 | 15 | 01、09 | [architecture.md](../architecture.md) |
| Agent 能力与任务完成度 | 20 | 02、04、05 | [agent-flow.md](../agent-flow.md)、[error-handling.md](../error-handling.md) |
| 工程质量与 Demo | 20 | 03、06 | [evidence.md](../evidence.md)、[observability.md](../observability.md) |
| 成本与性能 | 10 | 07 | [cost-and-performance.md](../cost-and-performance.md) |
| 答辩表达 | 15 | 全部 | [defense-guide.md](../defense-guide.md) |

## 3. 重新生成

```bash
python scripts/render_diagrams.py   # 只依赖标准库，生成同目录下的 SVG
python scripts/render_png.py        # 用本机 Edge/Chromium 无头模式生成 png/
```

- SVG 由 `scripts/render_diagrams.py` 直接拼装，不依赖 Graphviz、Mermaid 或任何 Python 绘图库。
- PNG 由 `scripts/render_png.py` 调用本机浏览器栅格化；Windows 默认查找 Edge，其他平台可传 `--browser /usr/bin/chromium`。
- 修改文案或数据时只改 `render_diagrams.py`，不要手改 SVG，否则下次生成会被覆盖。
- 字体使用系统自带的 Microsoft YaHei / Segoe UI / Noto Sans SC；没有这些字体的机器会回退到 sans-serif，版式不变。

## 4. 制图约定

- **尺寸**：正文图 1500 px 宽，部署图 1300 px 宽，按 1× 输出，PPT 里可无损放大。
- **颜色**：蓝色 = 检索 / 请求；紫色 = 报告 / MCP；橙色 = 后台任务 / 降级；青色 = 产物 / 存储；红色 = 失败 / 终止。
- **数字**：07、08、06 中的耗时、条数、成本来自 `docs/evidence.md` 记录的真实运行；人工耗时是估算，图内已标注口径。
- **不含内容**：图中不出现原项目名、内部域名、账号标识，也不出现未实现的能力。

## 5. 答辩时的讲法

- 先用 08 建立“为什么值得做”，再用 01 说明“不是脚本堆出来的”，最后用 02/03 讲“Agent 的任务拆解与失败隔离”。
- 被问“报告为什么不做成第七段”时，切到 03：独立 `run_id`、独立日志、独立失败域，目录不被报告拖住。
- 被问“外部接口挂了怎么办”时，切到 05：单源失败隔离、模型批次降级、报告失败保留旧产物。
- 被问“成本”时，切到 07：学术 API 为 0，主要成本是可选模型调用，一次演示不到 0.5 元。
