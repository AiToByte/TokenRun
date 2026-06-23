现在，我们正式从“设计图纸”转向“工程建设”。

为了让你能立即开始编码并看到效果，下一步我将为你提供 **TokenRun 的核心脚手架代码 (Scaffolding)**。我们将建立项目的物理结构，并实现最核心的 **Actor-Critic 循环引擎**。

---

### 第一步：建立项目结构 (Project Directory)

建议按照以下结构初始化你的 Git 仓库：

```text
TokenRun/
├── runfiles/               # 存放用户定义的 .yaml 任务蓝图
├── vault/                  # 固化后的 .trs 技能库
├── logs/                   # 执行轨迹 (Trace) 的持久化存储
├── core/
│   ├── __init__.py
│   ├── models.py           # Pydantic 协议模型 (之前已定义)
│   ├── runner.py           # 核心执行引擎
│   ├── orchestrator.py     # 任务编排逻辑
│   └── ledger.py           # Token 计费与审计
├── gateway/
│   ├── __init__.py
│   ├── file_gateway.py     # 本地文件读取与脱敏
│   └── provider.py         # LLM 客户端封装 (OpenAI/Claude)
├── main.py                 # CLI 入口
└── requirements.txt        # 依赖项
```

---

### 第二步：安装核心依赖 (`requirements.txt`)

```text
pydantic>=2.0.0
pyyaml
httpx
python-dotenv
jinja2
rich
```

---

### 第三步：核心引擎实现：`ActorCriticLoop` (最小原型)

这是 **Loop Engineering** 的代码级实现。它不依赖复杂的框架，直接体现了“执行-评价-修正”的闭环。

```python
# core/runner.py

import asyncio
from typing import Dict, Any, Optional
from .models import TaskNode, EvaluationResult, ExecutionIteration

class ActorCriticLoop:
    def __init__(self, actor_client, critic_client):
        self.actor = actor_client    # 昂贵模型 (如 Claude 3.5)
        self.critic = critic_client  # 廉价模型 (如 GPT-4o-mini)

    async def run(self, node: TaskNode, input_data: str) -> Dict[str, Any]:
        """执行 Actor-Critic 闭环"""
        attempts = 0
        current_feedback = ""
        iterations = []

        while attempts < node.loop_config.max_attempts:
            attempts += 1
            
            # 1. Actor 尝试生成结果
            # 我们将 feedback 注入到 Prompt 中
            actor_output = await self.actor.generate(
                prompt_template=node.actor_prompt_template,
                data=input_data,
                feedback=current_feedback
            )

            # 2. Critic 进行审计
            eval_result: EvaluationResult = await self.critic.evaluate(
                output=actor_output,
                criteria=node.loop_config.exit_criteria
            )

            # 3. 记录轨迹
            iterations.append({
                "iteration": attempts,
                "output": actor_output,
                "passed": eval_result.passed,
                "critique": eval_result.critique
            })

            # 4. 判定是否退出
            if eval_result.passed:
                return {"status": "success", "final_output": actor_output, "history": iterations}
            
            # 如果不通过，更新反馈，进入下一轮
            current_feedback = eval_result.critique
            print(f"      [Iteration {attempts}] Failed. Reason: {current_feedback}")

        return {"status": "exhausted", "final_output": actor_output, "history": iterations}
```

---

### 第四步：编写一个可运行的测试蓝图 (`test_mission.yaml`)

我们将测试一个简单的“总结并提取金句”的任务。

```yaml
# runfiles/test_mission.yaml
metadata:
  name: "Simple_Refinery_Test"
  version: "1.0"

workflow:
  - id: "summarizer"
    name: "深度总结"
    actor_prompt_template: "请为以下内容写一个精简的总结，并提取至少3个金句：{{data}}"
    loop_config:
      max_attempts: 3
      exit_criteria:
        - type: "llm_eval"
          criteria: "总结必须超过100字，且金句必须用【】括起来。"
```

---

### 第五步：CLI 入口：体验“1% 采样”逻辑 (`main.py`)

这个入口程序将展示系统如何根据采样配置只处理少量数据。

```python
# main.py

import asyncio
from core.models import Runfile
from core.runner import ActorCriticLoop
# 假设你已经写好了简单的 Client 封装
from gateway.provider import LLMClient 

async def main():
    # 1. 加载蓝图
    with open("runfiles/test_mission.yaml", "r") as f:
        import yaml
        config = yaml.safe_load(f)
        runfile = Runfile(**config)

    # 2. 模拟数据源
    raw_data = ["文章内容1...", "文章内容2...", "文章内容3...", "文章内容4..."]
    
    # 3. 执行 1% 采样 (这里假设采样 1 条)
    sample_data = raw_data[:1] 
    print(f"--- 开启 1% 采样模式 (处理 {len(sample_data)} 条数据) ---")

    # 初始化引擎
    # 注意：实际需填入你的 API Key 和 URL
    actor = LLMClient(model="claude-3-5-sonnet")
    critic = LLMClient(model="gpt-4o-mini")
    loop_engine = ActorCriticLoop(actor, critic)

    for node in runfile.workflow:
        for item in sample_data:
            result = await loop_engine.run(node, item)
            print(f"任务结果: {result['status']}")
            if result['status'] == "success":
                print(f"产出预览: {result['final_output'][:100]}...")

if __name__ == "__main__":
    asyncio.run(main())
```

---

### 🚀 你的下一步行动 (The Action)

现在，你已经具备了项目的基本骨架。为了让这个“炼金厂”跑起来，你目前需要完成的**最关键编码工作**是：

1.  **实现 `gateway/provider.py`**：编写一个能发送 HTTP 请求到 LLM 的简单客户端（支持配置不同的 `base_url`）。
2.  **细化 `Critic` 的评估逻辑**：在 `evaluate` 函数中，构造一个特定的 System Prompt，让廉价模型根据蓝图里的 `criteria` 返回 JSON 格式的通过报告。

**这就是 TokenRun 的 0 到 1。**

一旦你跑通了这个“翻译/总结并自我修正”的闭环，我们就已经攻克了最难的**逻辑壁垒**。剩下的工作（脱敏、计费、UI 指挥塔）都是基于这个闭环的**工程增强**。

**准备好开始写你的第一个 `provider.py` 吗？**