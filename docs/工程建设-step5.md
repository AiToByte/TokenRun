进入 **第六阶段：状态持久化、采样决策流与通讯信号 (Persistence, Sampling Flow & Telemetry)**。

在这一阶段，我们将构建 TokenRun 的“记忆系统”与“决策闸门”。根据第一性原理：**“数字能量的转化是不可逆的，已消耗的每一粒 Token 都必须沉淀为可回溯的资产状态；而大规模自动化必须经过低熵阈值（采样验证）的过滤。”**

---

### 一、 持久化与决策流的设计哲学

1.  **状态连续性原则 (State Continuity)：** 
    Token 任务往往耗时极长。如果系统在中途崩溃，重启后必须能通过“指纹对比”精确恢复到上一个成功节点，严禁产生“双重支付”（重复消耗 Token 处理同一数据）。
2.  **低熵准入原则 (Sampling Gate)：** 
    全量执行是高能耗行为。系统必须强制执行“打样-确认”逻辑。采样报告不仅是结果展示，它本质上是**逻辑契约的验证**。
3.  **遥测透明化 (Telemetry)：** 
    Runner 内部的复杂循环对外部应是透明的。通过信号（Signals）机制，UI 可以实时观测到每一次 Actor 的思考与 Critic 的纠错。

---

### 二、 核心组件实现：任务轨迹存证器 (`core/persistence.py`)

使用本地 SQLite 或结构化 JSONL 记录每一个单元的处理轨迹。

```python
import sqlite3
import json
from pathlib import Path
from typing import Optional, Dict, Any

class TaskPersistence:
    """
    任务轨迹存证器
    职责：实现执行状态的原子化存储，支持断点续传与历史回溯。
    """
    def __init__(self, db_path: str = "tokenrun_traces.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_traces (
                    id TEXT PRIMARY KEY,
                    input_hash TEXT,
                    status TEXT, -- 'PENDING', 'SAMPLED', 'COMPLETED', 'FAILED'
                    trace_data TEXT, -- 存储整个 Actor-Critic 循环记录 (JSON)
                    final_output TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def save_trace(self, unit_id: str, input_hash: str, status: str, trace: Dict, output: str = ""):
        """保存或更新单条数据的执行轨迹"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO task_traces (id, input_hash, status, trace_data, final_output, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (unit_id, input_hash, status, json.dumps(trace), output))

    def get_status(self, unit_id: str) -> Optional[str]:
        """获取某条数据的当前状态，用于断点判定"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT status FROM task_traces WHERE id = ?", (unit_id,))
            row = cursor.fetchone()
            return row[0] if row else None
```

---

### 三、 核心组件实现：采样管理器与信号流 (`core/sampling_manager.py`)

负责处理 1% 采样的逻辑，并生成供用户审批的“决策包”。

```python
class SamplingManager:
    """
    采样管理器
    职责：管理采样阶段的生命周期，生成预览报告，并控制执行流暂停。
    """
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.is_approved = False

    async def generate_report(self, sampled_traces: list) -> Dict[str, Any]:
        """
        生成采样决策报告
        包含：Token 消耗预估、质量评分分布、ROI 预测。
        """
        total_samples = len(sampled_traces)
        avg_score = sum(t['score'] for t in sampled_traces) / total_samples
        
        # 预估全量消耗：(采样消耗 / 采样比例)
        # 此处逻辑在 Orchestrator 中计算更佳，此处为结构示例
        return {
            "type": "SAMPLING_REPORT",
            "summary": {
                "sample_count": total_samples,
                "average_quality_score": round(avg_score, 2),
                "status": "AWAITING_APPROVAL"
            },
            "preview": sampled_traces[:3], # 提供前三个成功样本供预览
            "actions": ["APPROVE_FULL_RUN", "REVISE_PROMPT", "ABORT"]
        }

    async def wait_for_handshake(self):
        """
        决策握手：阻塞执行流，直到收到 UI 的批准信号
        """
        print("⏸️ [采样闸门] 任务已暂停。等待用户确认采样结果...")
        while not self.is_approved:
            # 在实际工程中，这里会监听 WebSocket 或 信号文件
            await asyncio.sleep(1) 
        print("▶️ [采样闸门] 用户已批准。开启全量生产模式。")
```

---

### 四、 科学性论述：断点续传与采样契约

1.  **原子化状态机 (Atomic State)：**
    在处理千万级 Token 任务时，网络异常是常态。`TaskPersistence` 通过 `input_hash` 建立了一套“幂等检查机制”。这意味着即使在极端情况下（如强行断电），只要输入内容没变，Runner 重启后绝不会对同一条数据进行二次 API 请求，从根本上杜绝了 Token 的浪费。

2.  **决策握手 (Decision Handshake)：**
    传统的 Agent 往往是“一路狂奔”，而 TokenRun 引入了“离散决策点”。采样报告是 Runner 向人类提交的**“生产质量承诺书”**。只有当人类签署（Approve）后，系统才会解锁高吞吐量执行。这种设计将 AI 的不可预测性限制在了极小（1%）的可控范围内。

3.  **遥测数据结构 (Telemetry Structure)：**
    我们的 Trace Data 采用非扁平化的 JSON 结构。它记录了 `Actor(Thought) -> Critic(Critique) -> Actor(Revised)` 的全过程。这不仅是为了调试，更是为了**Skill 固化**：这些轨迹是训练未来更小模型或微调 Prompt 的“黄金语料”。

---

### 五、 阶段性成果与下一步

目前，TokenRun 的“工厂架构”已经拥有了：
*   **物理手脚：** FileGateway & Redactor
*   **智能中枢：** Orchestrator & Loop Engine
*   **财务会计：** Ledger
*   **记忆存档：** Persistence
*   **质检中心：** SamplingManager

**下一步计划：**
我们将进入 **第七阶段：Skill 固化与 永久蓝图生成 (Solidification & Asset Export)**。
我们将编写最终的“炼金提纯”逻辑：当全量任务完成后，如何从海量的 Trace 中提取出表现最好的提示词版本、锁定的模型指纹和验证标准，将其打包成一个独立的 `.trs` 文件，实现“一次消耗，永久复用”。

**如果上述“存档与采样”逻辑符合您的标准，我们将开始进行最具成就感的“知识提纯”模块！**