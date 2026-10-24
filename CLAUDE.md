# Sextant / 六分仪

> 本文件是 Sextant 项目的项目级规范。工作区根目录的 [CLAUDE.md](../../CLAUDE.md) 是**根本性权限与总规范**，本文件不得与其相悖；两者冲突时，以根目录 CLAUDE.md 为准。本项目是本工作区的重点交付项目，规范与目录约定具有强制指导性，任何改动前先读本文件。

## 项目是什么

Sextant 是一个**研究情报追踪 Agent**：给定一个研究主题，自动完成「查询规划 → 多源检索 → 去重归一 → 相关性排序 → 生成可筛选的证据目录」，并把整条链路通过 Web 控制台、SSE 实时进度、MCP 工具接口和可观测日志暴露出来。

目标场景是参赛作品（赛道 B：AI Application），因此**可运行、可复现、可讲解**优先于功能数量。

## 目录约定

```
sextant/
├── CLAUDE.md                 本文件
├── README.md                 对外说明：定位、架构、启动、演示路径
├── pyproject.toml            包元数据与工具配置
├── requirements.txt          运行依赖（含版本区间）
├── .env.example              环境变量样例，不含真实密钥
├── Dockerfile
├── docker-compose.yml
├── sextant/
│   ├── config.py             所有环境变量集中读取，禁止在业务代码里直接 os.getenv
│   ├── engine/               从既有能力移植的检索/解析/渲染内核，保持纯函数风格
│   ├── pipeline/             垂直切片：plan → retrieve → normalize → rank → render
│   ├── llm.py                OpenAI 兼容接口封装（可选能力，缺失时降级）
│   ├── runlog.py             JSONL 运行日志 + 内存事件总线
│   ├── api/                  FastAPI 应用、路由、后台任务、SSE
│   ├── mcp/                  MCP server，把 engine 能力暴露为工具
│   └── web/index.html        单文件控制台（中文）
├── tests/                    单元 + API 冒烟测试
├── docs/                     架构、Agent 流程、异常处理、可观测性、成本性能
└── scripts/                  开发与演示辅助脚本
```

**分层依赖方向固定为**：`web → api → pipeline → engine`。`engine` 不允许反向 import `api`/`pipeline`；`pipeline` 不允许直接发 HTTP 请求（必须走 `engine.sources`）；`api` 不允许解析外部响应体。

## 命名红线

- 仓库是**公开仓库**，代码、注释、文档、User-Agent、日志、HTML 页脚中**不得出现**原项目名、公司名、内部域名、内部路径、同事姓名、平台账号标识。
- 对外统一使用 `Sextant` / `sextant`；Python 包名 `sextant`。
- 新增外部请求时必须使用 `config.USER_AGENT`，不得硬编码字符串。

## 开发约定

- Python 3.11+，类型标注齐全，优先标准库；新增依赖必须写进 `requirements.txt` 并说明理由。
- 每个 pipeline 阶段必须是**可重入**的：输入来自 workspace 文件，输出写回 workspace 文件。任何阶段失败都不得留下半截状态被下一阶段误用。
- 所有对外部服务的调用必须有超时、重试上限和降级路径；失败要写进 `logs/run.jsonl`，不能只抛异常。
- LLM 是**增强项不是必需项**：未配置 key 时整条链路必须照常可用。
- 改完必须跑 `pytest`；涉及前端时还要本地起服务验证一次真实流程。
- 密钥只从环境变量读取，`.env` 必须在 `.gitignore` 中。
- commit message 用英文；`git push` 只在用户明确要求时执行。

## 安全提示义务

按根目录 CLAUDE.md：每次完成功能后，必须主动提示用户是否需要执行 `codex-security` 安全审查，但**不得自动执行**。
