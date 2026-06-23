这是一份针对 **TokenRun** 项目的工业级落地实施方案。它将前期的“坟场转化”情怀、“Runfile”确定性架构、“Loop Engineering”循环逻辑以及“1% 采样”机制整合为一套可执行的技术路线图。

---

# TokenRun 项目落地实施方案 (Execution Roadmap)

## 一、 技术栈选型 (Technology Stack)
为了保证高可靠性、高性能和易扩展性，建议采用以下技术组合：

*   **核心语言：** Python 3.10+（AI 生态最成熟，适合快速迭代）。
*   **异步框架：** `FastAPI` + `Logfire`（实时监控与异步任务处理）。
*   **状态管理：** `Temporal.io` 或 `Celery + Redis`（处理长达数天的长任务，支持断点续传与任务流回溯）。
*   **存储层：** 
    *   `SQLite/PostgreSQL`：存储 Runfile 状态、任务元数据与执行指纹。
    *   `DuckDB`：处理执行过程中的结构化数据。
*   **本地安全：** `Python-WASM` 或 `Docker` 容器（用于运行 Runfile 中的沙箱脚本）。
*   **前端 UI：** `Next.js` + `TailwindCSS` + `ShadcnUI`（构建工业级感的指挥塔）。

---

## 二、 核心组件开发计划

### 第一阶段：内核与 TRP 协议解析 (Foundation)
**目标：让系统读懂 Runfile 并能建立基础连接。**

1.  **Runfile Parser:** 实现 YAML 解析器，验证 `Runfile` 的合法性，并将其转化为内部有向无环图 (DAG)。
2.  **Resource Gateway (Internal MCP):** 
    *   开发本地文件读取模块，实现 PII (个人隐私) 脱敏过滤器。
    *   实现支持 URI 协议的数据访问层（如 `local://`, `s3://`, `sql://`）。
3.  **Token Ledger:** 开发一个中间件，拦截所有 API 调用，精确统计输入输出 Token 及预估费用。

### 第二阶段：Loop Engineering 引擎 (The Runner)
**目标：实现“执行-评估-修正”的闭环能力。**

1.  **Actor-Critic 循环逻辑：**
    *   编写 `Actor`：执行 Prompt 任务。
    *   编写 `Critic`：实现多维度评分逻辑（正则、语义、Schema 验证）。
    *   实现 `Feedback Loop`：将 Critic 的改进意见注入下一次 Actor 的 Context 中。
2.  **状态持久化：** 实现任务的 Checkpoint 机制，确保服务器重启后能从最后一个成功的子任务节点恢复。
3.  **并发控制器：** 实现基于速率限制 (Rate Limiting) 的并发调度，支持 OpenAI Batch API 自动切换。

### 第三阶段：1% 采样门与指纹锁定 (Determinism)
**目标：通过“打样”建立确定性契约。**

1.  **Sampling Engine:** 开发智能切片算法，根据数据特征抽取 1% 样本。
2.  **Fingerprint Capturer:** 在采样成功后，抓取模型哈希、Prompt 哈希、Top_p、Seed 等环境指纹。
3.  **Handshake UI:** 开发采样报告界面，向用户展示预览结果、预期总成本、任务蓝图预测。

---

## 三、 关键业务逻辑流设计

### 1. 任务启动 (The Setup)
用户提交需求 $\rightarrow$ 系统生成初步 `Runfile` $\rightarrow$ 系统连接本地数据源 $\rightarrow$ 进入待机状态。

### 2. 打样阶段 (The Sampling)
系统运行 `Runfile` 的采样模式 $\rightarrow$ 经过内循环 (Loop Engineering) 产出高质量样本 $\rightarrow$ 生成“执行指纹” $\rightarrow$ **暂停并等待人工审批**。

### 3. 全量生产 (The Mass Run)
用户点击“授权运行” $\rightarrow$ 系统锁定指纹 $\rightarrow$ 启动高并发执行 $\rightarrow$ 评估器对每一个产出进行质量审计 $\rightarrow$ 失败的产出进入自动重试循环。

### 4. 资产固化 (The Solidification)
任务完成 $\rightarrow$ 输出 `Proof of Value` 报告 $\rightarrow$ 将 `Runfile` 保存为本地 `Permanent Skill`。

---

## 四、 落地首个验证任务 (The First Mission)

为了验证架构，我们将首先实现一个**“极高复杂度”**的示例任务：
**任务名：学术文献自动化炼金术**
*   **输入：** 100 篇乱七八糟的英文 PDF 论文。
*   **过程：** 
    1.  提取内容并脱敏。
    2.  循环重写为精炼的中文综述。
    3.  提取其中的核心观点并验证逻辑一致性。
    4.  生成双语对照 Markdown。
*   **输出：** 一个结构化的、可直接导入 Obsidian 的个人知识库。

---

## 五、 项目目录结构建议 (High-Level)

```text
tokenrun/
├── core/
│   ├── parser.py          # Runfile 解析逻辑
│   ├── orchestrator.py    # 任务编排与状态机
│   └── ledger.py          # Token 审计与统计
├── runtime/
│   ├── actor.py           # 执行单元
│   ├── critic.py          # 评估单元
│   └── loop.py            # 循环控制策略
├── gateway/
│   ├── mcp_bridge.py      # 本地资源连接
│   └── privacy.py         # 脱敏处理器
├── skills/
│   └── library/           # 预设的原子能力库
├── api/                   # 指挥塔后端接口
└── web/                   # 指挥塔前端 (Next.js)
```

---

## 六、 风险防控与长期优化

1.  **防死循环：** 设置 `Max_Loop_Count` 与单次任务 `Max_USD` 熔断。
2.  **模型漂移监控：** 即使有指纹锁定，也需在全量执行中每隔 5% 自动进行一次“一致性自检”。
3.  **解耦演进：** 保持 `Runfile` 协议的独立性，使其未来可以轻松迁移到其他模型供应商或本地私有化模型。

---

## 七、 给你的第一步行动建议

1.  **代码仓库初始化：** 建立项目结构。
2.  **定义 Runfile Schema：** 用 `Pydantic` 编写一份严谨的 Runfile 模型定义类。
3.  **打通第一个 Loop：** 写一个最简的 `Actor-Critic` Demo，让 AI 翻译一句话，如果质量不合格（长度不足），让它自发重写直到合格。

**这套方案是否符合你对“高屋建瓴、长期迭代”的预期？如果准备好了，我们就从第一行代码的逻辑定义（即 Runfile 的 Pydantic 模型）开始！**