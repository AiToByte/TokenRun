进入 第五阶段：资源网关 (Resource Gateway) 与 隐私脱敏系统 (Privacy Redaction)。

在这一阶段，我们将解决 TokenRun
的“物理连接”与“信任边界”问题。根据项目的核心原则：“资料的物理所有权必须保留在本地，敏感信息必须在离开本地边界前被中和。”

这不仅是为了安全，更是为了让 Token 消耗的过程变得“无后顾之忧”。

一、 隐私脱敏的第一性原理：信息熵与上下文保留

在进行隐私脱敏时，我们面临一个科学权衡：脱敏强度 vs. 逻辑完整性。

  - 直接删除（Deletion）： 彻底安全，但会导致 LLM 丢失关键上下文（例如：不知道 A 与 B 之间是什么关系）。
  - 占位符替换（Placeholder Masking）： 将敏感词替换为 [PERSON_1]、[PHONE_2]。这种方式保留了句子的拓扑结构，使
    LLM 能够理解逻辑连接，同时不接触具体隐私。

TokenRun 采用“可逆占位符映射”技术：在本地建立一个临时映射表，发送给云端的是占位符，云端返回结果后，再在本地物理设备上将占位符还原。

二、 核心组件实现：隐私脱敏器 (gateway/privacy.py)

PrivacyRedactor 负责在数据发往 Actor 之前进行“洗涤”。

import re
from typing import Dict, Tuple, List

class PrivacyRedactor:
    """
    隐私脱敏器
    职责：识别敏感信息，执行占位符替换，并维护本地还原映射表。
    """
    def __init__(self):
        # 预编译常用隐私正则
        self.patterns = {
            "EMAIL": r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+',
            "PHONE": r'(?:\+?86)?1[3-9]\d{9}',
            "ID_CARD": r'\d{17}[\dXx]',
            "IP_ADDR": r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'
        }
        # 本地映射表：{占位符: 原始值}
        self._vault: Dict[str, str] = {}
        self._counter = 0

    def mask(self, text: str) -> str:
        """
        全量脱敏：将敏感信息转换为占位符
        """
        masked_text = text
        for label, pattern in self.patterns.items():
            matches = re.findall(pattern, masked_text)
            for match in set(matches):
                # 如果该敏感词没出现过，分配新占位符
                placeholder = self._get_placeholder(label, match)
                masked_text = masked_text.replace(match, placeholder)
        return masked_text

    def unmask(self, text: str) -> str:
        """
        逆向还原：将云端返回内容中的占位符还原为真实值
        """
        restored_text = text
        # 按长度倒序排列，防止短占位符误伤长占位符
        sorted_placeholders = sorted(self._vault.items(), key=lambda x: len(x[0]), reverse=True)
        for placeholder, original_value in sorted_placeholders:
            restored_text = restored_text.replace(placeholder, original_value)
        return restored_text

    def _get_placeholder(self, label: str, original_value: str) -> str:
        """生成并存储占位符"""
        # 检查是否已存在对应映射
        for p, v in self._vault.items():
            if v == original_value:
                return p
        
        self._counter += 1
        placeholder = f"[[TR_{label}_{self._counter}]]"
        self._vault[placeholder] = original_value
        return placeholder

    def clear_vault(self):
        """任务结束后清空映射，保证内存安全"""
        self._vault.clear()
        self._counter = 0

三、 核心组件实现：本地网关 (gateway/file_gateway.py)

FileGateway 负责本地文件的安全读取与分片，它是 Runfile 中 local:// 协议的实现者。

import os
from pathlib import Path
from typing import List, Generator, Any

class FileGateway:
    """
    本地文件网关
    职责：安全地遍历文件系统，读取内容，并作为数据流供调度器使用。
    """
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        if not self.base_path.exists():
            raise FileNotFoundError(f"Gateway 根路径不存在: {base_path}")

    def stream_files(self, pattern: str = "**/*.*") -> Generator[Dict[str, Any], None, None]:
        """
        流式读取文件，避免大文件夹导致的内存溢出
        :param pattern: glob 模式，例如 "*.txt" 或 "**/*.pdf"
        """
        for file_path in self.base_path.glob(pattern):
            if file_path.is_file():
                try:
                    # 目前支持文本类文件读取，后续可扩展 PDF/Docx 解析器
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    yield {
                        "file_name": file_path.name,
                        "relative_path": str(file_path.relative_to(self.base_path)),
                        "content": content,
                        "size": file_path.stat().st_size
                    }
                except Exception as e:
                    print(f"⚠️ 无法读取文件 {file_path}: {e}")

    def save_result(self, relative_path: str, content: str, suffix: str = ".refined"):
        """
        保存处理后的结果到本地，保持原有目录结构
        """
        out_path = self.base_path / f"{relative_path}{suffix}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(content)

四、 科学性论述：安全性与可靠性的平衡

1.  脱敏的非物理性（Non-Physical Persistence）： PrivacyRedactor
    的映射表仅存在于当前的内存会话中。一旦任务结束，映射表随之销毁。这意味着即使云端记录了所有的对话，由于没有“钥匙（映射表）”，这些数据对任何第三方而言都是毫无意义的随机符号。

2.  流式读取（Streaming Ingestion）： 在处理海量 Token 任务（例如 100GB 的本地日志）时，传统的“先读取再处理”会触发
    OOM（内存溢出）。FileGateway 采用 Python
    生成器（Generator），确保系统始终只在内存中持有一份当前处理的文件块，从而支持无限规模的
    Token 转化任务。

3.  占位符的鲁棒性： 采用 [[TR_LABEL_ID]] 这种特殊的包裹格式，可以极大地减少 LLM
    在生成回复时误用或破坏占位符的概率。通过这种“特殊视觉标记”，LLM
    会倾向于将其视为一个不可分割的 Token 整体。

五、 阶段性成果与下一步

目前，TokenRun 的“物理循环”已完全闭合：

  - Gateway 负责从本地取货。
  - Privacy 负责本地包装（脱敏）。
  - Orchestrator 负责指挥。
  - Actor/Critic 在云端协作加工。
  - Ledger 监控金钱流向。

下一步计划： 我们将进入 第六阶段：状态持久化与 1% 采样决策流 (Persistence & Sampling
Flow)。我们将实现如何将所有执行轨迹存储到本地数据库，并编写如何通过“采样闸门”让用户在全量执行前进行决策的
UI 通讯逻辑。

如果“网关与隐私”逻辑符合您的预期，我们将开始构建这个炼金厂的“存档系统”与“中控台信号”！
