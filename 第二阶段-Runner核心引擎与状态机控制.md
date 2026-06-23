在定义好“宪法”（数据模型）之后，我们进入最核心的工程实现——**第二阶段：Runner 核心引擎与状态机控制 (The Orchestrator Logic)**。

这个阶段我们要实现 `TRRunner` 类。它是整个系统的动力心脏，负责驱动任务从“采样”到“全量”的平滑过渡，并严格执行 **Loop Engineering**。

---

### 1. 设计核心：异步任务调度器 (TRRunner)

我们将采用 `async/await` 异步架构，确保在大规模并发 Token 消耗时，系统依然能保持高效的事件响应和状态监控。

```python
import asyncio
import hashlib
import time
from typing import Callable, Awaitable
from .models import Runfile, TaskTrace, ExecutionIteration, EvaluationResult, TaskNode

class TRRunner:
    def __init__(self, runfile: Runfile, storage_provider: Any):
        self.runfile = runfile
        self.storage = storage_provider  # 用于任务状态持久化
        self.is_paused = False
        self.current_trace: Dict[str, TaskTrace] = {}

    async def execute(self, mode: str = "sample"):
        """
        核心执行入口
        mode: "sample" (1%采样) | "full" (全量生产)
        """
        print(f"🚀 TokenRun 启动 - 模式: {mode}")
        
        # 1. 资源嗅探与连接 (Context Sensing)
        resources = await self._sense_resources()
        
        # 2. 遍历工作流节点
        for node in self.runfile.workflow:
            # 确定执行范围
            targets = self._prepare_targets(resources, mode)
            
            # 3. 驱动任务节点执行
            await self._run_node(node, targets)

    async def _run_node(self, node: TaskNode, targets: List[Any]):
        """执行单个工作流节点"""
        for target in targets:
            if self.is_paused:
                await self._wait_for_resume()
                
            # 执行原子级的 Actor-Critic 循环
            await self._execute_task_loop(node, target)

    async def _execute_task_loop(self, node: TaskNode, data: Any):
        """
        【Loop Engineering 核心实现】
        Actor-Critic 闭环逻辑
        """
        attempts = 0
        feedback = ""
        trace = TaskTrace(task_id=node.id, status="running", iterations=[])

        while attempts < node.loop_config.max_attempts:
            attempts += 1
            start_time = time.time()

            # --- Actor 阶段: 执行指令 ---
            # 这里的 actor_call 会调用实际的 LLM 接口
            output = await self._actor_call(node, data, feedback)
            
            # --- Critic 阶段: 质量评估 ---
            # 这里的 critic_eval 会根据 Runfile 定义的规则进行审计
            eval_result = await self._critic_eval(node, output)
            
            # 记录本次迭代轨迹
            iteration = ExecutionIteration(
                iteration_index=attempts,
                input_payload=str(data),
                output_content=output,
                evaluation=eval_result,
                tokens_consumed=self._get_usage_from_llm(),
                latency_ms=int((time.time() - start_time) * 1000),
                timestamp=str(time.time())
            )
            trace.iterations.append(iteration)
            
            # 持久化当前状态 (实现断点续传的关键)
            await self.storage.save_trace(trace)

            # 检查是否满足退出条件
            if eval_result.passed:
                trace.status = "completed"
                trace.final_output = output
                print(f" ✅ 任务 {node.id} 在第 {attempts} 次循环达成目标")
                break
            else:
                feedback = eval_result.critique
                print(f" ⚠️ 循环 {attempts} 未达标: {feedback}")
                
        if trace.status != "completed":
            trace.status = "failed"
            # 这里可以触发人工干预逻辑或自动标记
            
        return trace

    async def _actor_call(self, node: TaskNode, data: Any, feedback: str) -> str:
        """调用模型单元 (需集成 OpenAI/Claude API)"""
        # 实际开发中，这里会根据 node.actor_prompt_template 渲染 Prompt
        # 并将上一次的 feedback 注入作为修正建议
        pass

    async def _critic_eval(self, node: TaskNode, output: str) -> EvaluationResult:
        """质量审计单元 (基于校验规则)"""
        # 实现正则、Schema校验或 LLM-as-a-Judge
        pass
```

---

### 2. 关键工程逻辑详解

#### A. 状态持久化 (Checkpointing)
在 `_execute_task_loop` 中，每一次循环迭代后都会执行 `save_trace`。
*   **长期价值：** 如果全量执行任务需要消耗 1000 万 Token（耗时几小时），即使中途断网或停电，Runner 重启后通过扫描 `storage`，能立即发现哪些数据已处理完成，直接从断点继续，不会产生二次扣费。

#### B. 采样与全量的“切换快照”
*   在 `mode="sample"` 时，`_prepare_targets` 会根据 `runfile.sampling` 规则只切出 1% 的数据。
*   **指纹生成：** 当采样任务全部 `status="completed"` 时，Runner 会计算当前 Prompt 和模型的哈希值，将其存入 `fingerprint` 字段。
*   **授权检查：** 当进入 `mode="full"` 时，Runner 会先对比当前配置与 `fingerprint`。如果 Prompt 被改动过，Runner 会拒绝启动并提示：“检测到逻辑变动，请重新采样以确认质量”。

#### C. 动态反馈注入 (Feedback Injection)
在 Loop Engineering 逻辑中，如果 `passed=False`，下一次 `_actor_call` 会获得一个 `critique` 字段。
*   **实现细节：** 系统会自动在 System Prompt 中追加：*"你在上一次尝试中犯了如下错误：{feedback}。请在此轮中进行修正并确保满足所有校验规则。"*

---

### 3. 接下来：Runner 的资源连接与脱敏 (Gateway)

有了执行逻辑，下一步我们要解决“手”的问题。即：
1.  **资源抽象：** 如何让 `_sense_resources` 自动识别本地 PDF 或 SQL 数据库。
2.  **隐私脱敏：** 如何在 `_actor_call` 发送数据前，自动执行本地脱敏。
3.  **Token 计费：** 实现一个实时“计价器”，在每一轮迭代后精准更新账本。

---

### 🚀 开发者行动建议

你可以开始思考：**如果你要实现 `_critic_eval` 中的“语义一致性校验”，你会选择用简单的字符串匹配，还是再调用一次廉价模型（如 GPT-4o-mini）来进行打分？** 

这是 TokenRun 的一个关键平衡点：**用廉价 Token 的“审计”来保证高价值 Token 的“产出”。**

**如果上述架构逻辑清楚，我们开始细化 `Gateway`（网关）和 `Ledger`（账本）的实现？**