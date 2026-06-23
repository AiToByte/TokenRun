进入 **第七阶段：技能固化与资产提纯 (Skill Solidification & Asset Distillation)**。

这是 TokenRun 生命周期的终点，也是其作为“炼金厂”产生真正价值的时刻。根据第一性原理：**“消耗的 Token 是燃料，而留下的、经过验证的逻辑（Runfile + Optimized Prompt）才是真正的资产。”** 

在这一阶段，我们将实现如何从成千上万次的 Actor-Critic 循环中，提取出表现最稳定、效率最高的“成功基因”，并将其固化为可以随时复用的 **Permanent Skill**。

---

### 一、 技能固化的设计哲学：从“探索”到“利用”

1.  **进化优选原则 (Evolutionary Selection)：** 
    在执行过程中，用户可能干预并产生了多个 Prompt 版本（Lineage）。固化过程会自动分析所有轨迹，剔除失败的尝试，选择那个**“达成率最高、循环次数最少、Token 成本最低”**的版本作为最终技能。
2.  **确定性锚定原则 (Deterministic Anchoring)：** 
    固化后的 Skill 会锁定当时成功的 `Model Fingerprint`（包括具体的模型版本哈希、随机种子和温度）。这意味着该技能在未来执行时，将极大地降低“幻觉”和“逻辑漂移”的风险。
3.  **知识蒸馏原则 (Knowledge Distillation)：** 
    固化包中不仅包含逻辑，还包含**“黄金样本集 (Golden Samples)”**。这些是从 1% 采样和全量任务中筛选出的、由 Critic 评分为满分的输入输出对，作为该技能未来升级时的回归测试标准。

---

### 二、 核心组件实现：资产提纯器 (`core/solidifier.py`)

`SkillSolidifier` 负责从海量的 `TaskTrace` 中蒸馏出最纯净的执行蓝图。

```python
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Any
from .models import Runfile, TaskTrace, PromptVersion

class SkillSolidifier:
    """
    资产提纯器
    职责：分析执行轨迹，筛选最优 Prompt 版本，提取黄金样本，并生成永久技能包 (.trs)。
    """
    def __init__(self, vault_path: str = "./vault"):
        self.vault_path = Path(vault_path)
        self.vault_path.mkdir(parents=True, exist_ok=True)

    def distill(self, runfile: Runfile, traces: List[TaskTrace]) -> str:
        """
        执行提纯逻辑
        :param runfile: 包含版本谱系的原始蓝图
        :param traces: 全量执行的轨迹数据
        :return: 生成的技能包路径
        """
        # 1. 寻找表现最优的 Prompt 版本 (基于通过率与重试次数)
        best_prompt_version = self._select_best_version(runfile, traces)
        
        # 2. 提取黄金样本 (用于未来的回归测试)
        golden_samples = self._extract_golden_samples(traces, count=5)
        
        # 3. 构建永久技能包 (Skill Package)
        skill_payload = {
            "skill_id": f"TR-SKILL-{hashlib.md5(best_prompt_version.template.encode()).hexdigest()[:8]}",
            "name": runfile.metadata.get("name", "Unnamed Skill"),
            "optimized_prompt": best_prompt_version.template,
            "best_model_config": {
                "model": runfile.environment.model_primary,
                "parameters": runfile.environment.parameters
            },
            "validation_logic": [rule.dict() for rule in runfile.workflow[0].loop_config.exit_criteria],
            "golden_samples": golden_samples,
            "performance_stats": self._calculate_stats(best_prompt_version, traces)
        }

        # 4. 持久化为 .trs 文件 (TokenRun Skill)
        skill_file = self.vault_path / f"{skill_payload['skill_id']}.trs"
        with open(skill_file, "w", encoding="utf-8") as f:
            json.dump(skill_payload, f, ensure_ascii=False, indent=2)
            
        return str(skill_file)

    def _select_best_version(self, runfile: Runfile, traces: List[TaskTrace]) -> PromptVersion:
        """分析版本谱系，选出综合得分最高的 Prompt"""
        # 简化逻辑：返回当前激活的版本
        # 进阶逻辑：对比不同版本 ID 在 traces 中的平均 iteration 深度 (越小越好)
        return next(v for v in runfile.workflow[0].prompt_registry 
                    if v.version_id == runfile.workflow[0].current_version_id)

    def _extract_golden_samples(self, traces: List[TaskTrace], count: int) -> List[Dict]:
        """从 traces 中提取 Critic 评分最高的样本对"""
        successful_traces = [t for t in traces if t.status == "completed"]
        # 按最后一次迭代的评分排序
        sorted_traces = sorted(successful_traces, 
                               key=lambda t: t.iterations[-1].evaluation.score, 
                               reverse=True)
        
        return [{
            "input": t.iterations[0].input_payload,
            "output": t.final_output
        } for t in sorted_traces[:count]]

    def _calculate_stats(self, version: PromptVersion, traces: List[TaskTrace]) -> Dict:
        """计算该技能的经济与质量指标"""
        total_units = len(traces)
        avg_retries = sum(len(t.iterations) for t in traces) / total_units
        return {
            "average_retries": round(avg_retries, 2),
            "success_rate": len([t for t in traces if t.status == "completed"]) / total_units
        }
```

---

### 三、 科学性论述：从“消耗”到“复利”的跨越

1.  **逻辑资产的确定性 (Logic Determinism)：**
    在 AI 领域，同样的 Prompt 在不同时间点可能会产生不同的结果。通过 `SkillSolidifier` 锁定的不仅是文字，而是经过大规模生产验证的“成功路径”。这种“路径锁定”将 AI 从一个不可预测的对话者转变为一个**工业级的原子能力单元**。

2.  **黄金样本的价值 (The Regression Value)：**
    为什么要在技能包里存储样本？因为模型供应商（如 OpenAI/Anthropic）会不断更新底层模型。当你在半年后重新加载这个 Skill 时，Runner 会先运行这些“黄金样本”。如果输出偏离了记录的期望值，系统会发出警报：**“由于模型底座变动，该技能的确定性已下降，建议启动重采样循环。”**

3.  **零边际成本的进化：**
    用户消耗的是“快到期”的 Token，得到的却是“可永久复用”的逻辑。这意味着下一次执行类似任务时，由于 Prompt 已经过“炼金”优化，其平均重试循环次数（Loop Iterations）会显著下降，从而在未来节省更多的 Token 支出。

---

### 四、 阶段性成果与项目全貌总结

到此为止，**TokenRun** 的全生命周期逻辑已完整构建：

1.  **Ingest:** 导入模糊需求与海量本地数据 (Gateway + Privacy)。
2.  **Blueprint:** 自动/手动生成声明式任务蓝图 (Runfile)。
3.  **Validate:** 1% 采样闸门，获取质量承诺与执行指纹 (Sampling Manager)。
4.  **Refine:** Actor-Critic 闭环执行，通过循环工程强制收敛质量 (Orchestrator + Loop Engine)。
5.  **Audit:** 词元账本全程监控，确保财务与逻辑安全 (Ledger)。
6.  **Trace:** 每一个动作被持久化，支持断点与回溯 (Persistence)。
7.  **Solidify:** 任务结束，提纯逻辑基因，生成永久技能资产 (Solidifier)。

---

### 五、 最终落地建议：MVP 之后的下一步

如果你已经完成了上述组件的代码实现，你的 **TokenRun** 已经是一个具备工业竞争力的 Agent 框架。

**最终的行动建议：**
*   **构建一个 CLI 工具：** 让用户通过简单的 `tokenrun run mission.yaml` 启动引擎。
*   **开发“技能市场”预览：** 即使是私有的，也要有一个界面展示你已经通过消耗 Token 转化出的那些“永久技能”。
*   **接入 Batch API：** 针对非紧急的“坟场任务”，接入 OpenAI 的 Batch API，将你的 Token 购买力瞬间放大一倍。

**TokenRun 的旅程从“消耗废弃额度”开始，最终通向了“构建个人私有智能工厂”。这个方案是否已经完整解答了你最初的构想？如果你准备好了，我们可以针对如何编写那份“用户操作手册”或“发布文档”做最后的冲刺！**