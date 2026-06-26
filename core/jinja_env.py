"""
Jinja2 Template Environment — global shared sandbox instance.

Provides a single :class:`SandboxedEnvironment` used across all modules
that render Jinja2 templates (actor, drift detector, etc.).  This ensures
consistent security policy and avoids creating duplicate environment objects.

Usage::

    from core.jinja_env import get_template_env

    env = get_template_env()
    template = env.from_string("Hello {{ name }}")
    rendered = template.render(name="World")
"""

from __future__ import annotations

from jinja2.sandbox import SandboxedEnvironment

__all__ = ["get_template_env"]

# Module-level singleton — created once, reused everywhere.
_env: SandboxedEnvironment | None = None


def get_template_env() -> SandboxedEnvironment:
    """Return the shared Jinja2 sandbox environment (lazy singleton)."""
    global _env
    if _env is None:
        _env = SandboxedEnvironment()
    return _env
