"""
Task Actor — renders prompt templates and calls the expensive model.

The Actor is the "hands" of the loop: it takes a Jinja2 template from
the Runfile, injects data and optional Critic feedback, and returns the
generated text.
"""

from __future__ import annotations

from typing import Optional

from jinja2 import Template, TemplateSyntaxError, UndefinedError

from gateway.provider import LLMProvider, LLMResponse

__all__ = ["TaskActor"]


class TaskActor:
    """Execute a task by rendering a prompt template and calling the LLM.

    Parameters
    ----------
    provider:
        An :class:`LLMProvider` instance configured for the expensive model
        (e.g. Claude 3.5 Sonnet, GPT-4o).
    """

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def generate(
        self,
        template_str: str,
        data: str,
        feedback: str = "",
    ) -> LLMResponse:
        """Render the prompt and call the LLM.

        Parameters
        ----------
        template_str:
            A Jinja2 template from ``TaskNode.actor_prompt_template``.
            The variable ``{{ data }}`` is always available.
        data:
            The input payload (file content, CSV row, etc.).
        feedback:
            If non-empty, a correction directive from the Critic is
            prepended as a system message so the Actor can revise.

        Returns
        -------
        LLMResponse
            Raw response including content and token counts.

        Raises
        ------
        ValueError
            If the Jinja2 template has syntax errors or undefined variables.
        """
        try:
            rendered = Template(template_str).render(data=data)
        except (TemplateSyntaxError, UndefinedError) as exc:
            raise ValueError(f"Prompt 模板渲染失败: {exc}") from exc

        messages = []
        if feedback:
            messages.append({
                "role": "system",
                "content": (
                    "你在上一轮尝试中未达标。反馈如下：\n"
                    f"{feedback}\n\n"
                    "请根据反馈修正你的输出，确保满足所有校验规则。"
                ),
            })
        messages.append({"role": "user", "content": rendered})

        return await self.provider.request(messages=messages)
