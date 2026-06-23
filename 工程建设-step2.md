在这一阶段，我们将实现 **TokenRun** 的核心动力组件：`LLMProvider`（通用的模型接入层）与 `Critic`（基于廉价模型的质量审计器）。

这部分代码是系统与外部智能交互的“枢纽”，必须处理好**异步并发、错误重试、Token 消耗统计**以及**结构化解析**。

---

### 1. 实现通用模型接入层 (`core/provider.py`)

该组件负责封装不同厂商的 API 调用，支持自定义 URL 和模型参数，并确保输出的稳定性。

```python
import json
import httpx
import asyncio
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

class LLMResponse(BaseModel):
    """标准化的模型响应格式"""
    content: str
    prompt_tokens: int
    completion_tokens: int
    model_name: str

class LLMProvider:
    """
    通用 LLM 接入层
    支持自定义 API 地址、密钥及模型参数，内置重试机制。
    """
    def __init__(self, api_key: str, base_url: str, model_name: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.client = httpx.AsyncClient(timeout=60.0)

    async def request(
        self, 
        messages: List[Dict[str, str]], 
        temperature: float = 0.1,
        response_format: Optional[Dict] = None
    ) -> LLMResponse:
        """
        向 LLM 发起异步请求
        :param messages: 对话上下文
        :param temperature: 采样温度，默认为 0.1 以确保确定性
        :param response_format: 是否要求强制返回 JSON 格式
        """
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        # 简单的重试逻辑 (可扩展为指数退避)
        for attempt in range(3):
            try:
                response = await self.client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload
                )
                response.raise_for_status()
                data = response.json()

                return LLMResponse(
                    content=data["choices"][0]["message"]["content"],
                    prompt_tokens=data["usage"]["prompt_tokens"],
                    completion_tokens=data["usage"]["completion_tokens"],
                    model_name=self.model_name
                )
            except Exception as e:
                if attempt == 2:
                    raise Exception(f"LLM 请求失败 (已重试3次): {str(e)}")
                await asyncio.sleep(1 * (attempt + 1))

    async def close(self):
        """关闭 HTTP 客户端"""
        await self.client.aclose()
```

---

### 2. 实现质量审计器 (`core/critic.py`)

`Critic` 是 **Loop Engineering** 的灵魂。它通过廉价模型（如 GPT-4o-mini）按照 `Runfile` 定义的规则对 `Actor` 的产出进行“严苛审计”。

```python
import json
from typing import List, Dict
from .models import EvaluationResult, ValidationRule
from .provider import LLMProvider

class TaskCritic:
    """
    质量审计单元
    核心职责：利用廉价模型对 Actor 的输出进行结构化打分和反馈。
    """
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def evaluate(
        self, 
        task_name: str, 
        input_data: str, 
        output_content: str, 
        rules: List[ValidationRule]
    ) -> EvaluationResult:
        """
        根据规则集评估输出内容
        :param task_name: 任务名称
        :param input_data: 原始输入数据
        :param output_content: Actor 产生的结果内容
        :param rules: Runfile 中定义的校验规则列表
        """
        
        # 将规则列表转化为文本描述，供 LLM 理解
        rules_desc = "\n".join([f"- [{r.type}] 权重 {r.weight}: {r.criteria}" for r in rules])

        system_prompt = """你是一个专业的工业级质量审计员。
你的任务是根据用户提供的规则，对 AI 生成的内容进行审查。
你必须以 JSON 格式输出，包含以下字段：
- passed (boolean): 是否通过所有核心规则
- score (float): 综合评分 (0.0 - 1.0)
- critique (string): 如果不合格，请详细说明原因；如果合格，请留空
- suggestions (list of strings): 具体的改进建议
"""

        user_content = f"""
请评估以下任务产出：
【任务名称】：{task_name}
【原始输入】：{input_data}
【待评估产出】：{output_content}

【必须遵循的审计规则】：
{rules_desc}

请基于上述标准给出你的审计报告。
"""

        response = await self.provider.request(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"}
        )

        try:
            # 解析审计结果
            report = json.loads(response.content)
            return EvaluationResult(
                passed=report.get("passed", False),
                score=report.get("score", 0.0),
                critique=report.get("critique", ""),
                suggestions=report.get("suggestions", []),
                # 这里可以额外记录此次审计消耗的 Token
                audit_cost=response.completion_tokens + response.prompt_tokens 
            )
        except json.JSONDecodeError:
            return EvaluationResult(
                passed=False, 
                score=0.0, 
                critique="审计报告格式异常，强制重新循环"
            )
```

---

### 3. 实现执行器包装器 (`core/actor.py`)

`Actor` 负责将用户的 `Runfile` 模板渲染成最终的 Prompt 并获取结果。

```python
from jinja2 import Template
from .provider import LLMProvider

class TaskActor:
    """
    执行单元
    核心职责：渲染提示词并调用昂贵模型获取初步结果。
    """
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def generate(
        self, 
        template_str: str, 
        data: str, 
        feedback: str = ""
    ) -> str:
        """
        渲染 Prompt 并执行任务
        :param template_str: Runfile 中的 Jinja2 模板
        :param data: 输入数据
        :param feedback: 来自上一次 Critic 的改进建议
        """
        # 使用 Jinja2 渲染模板，支持复杂的 Prompt 逻辑
        template = Template(template_str)
        rendered_prompt = template.render(data=data)

        messages = []
        # 如果存在反馈，说明是重试循环，注入修正逻辑
        if feedback:
            messages.append({
                "role": "system", 
                "content": f"你在上一轮尝试中未达标。反馈如下：{feedback}\n请根据反馈修正你的输出。"
            })
        
        messages.append({"role": "user", "content": rendered_prompt})

        response = await self.provider.request(messages=messages)
        return response.content
```

---

### 4. 这一步的设计亮点

1.  **确定性优先：** `LLMProvider` 默认 `temperature=0.1`。对于“生产流水线”而言，稳定性和可预测性远比“创意”重要。
2.  **Jinja2 引擎：** `Actor` 采用 Jinja2 模板。这意味着你的 `Runfile` 不仅可以包含简单的字符串，还可以包含条件判断、列表循环等复杂的 Prompt 工程逻辑。
3.  **闭环负反馈：** 在 `Actor` 的逻辑中，我们专门处理了 `feedback` 参数。这保证了 **Loop Engineering** 能够真正发挥作用——AI 不是在“盲目重试”，而是在“听取意见后改进”。
4.  **低成本审计：** `Critic` 强制使用结构化 JSON 输出，极大地方便了后端的自动化逻辑判断。

---

### 🚀 后续计划

目前，“手（Actor）”和“眼（Critic）”都已经就位。下一步我们要实现：

**第四阶段：词元账本 (The Ledger) 与 并发控制中心 (Orchestrator)**

我们将编写如何统筹这套 Actor-Critic 逻辑，在面对 1000 条数据时，如何利用并发能力高速处理，同时保证不超出用户的预算限额。

**如果你已经理解了这一层逻辑，我们将开始编写最复杂的 `Orchestrator.py` —— 也就是那个指挥所有组件“大合唱”的任务编排器。准备好了吗？**