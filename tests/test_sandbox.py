"""Tests for core.sandbox — subprocess code execution."""

import pytest
from core.sandbox import SandboxExecutor


@pytest.fixture
def sandbox():
    return SandboxExecutor(timeout=5)


class TestSandboxExecutor:
    def test_execute_pass(self, sandbox):
        result = sandbox.execute_python("assert 1 + 1 == 2")
        assert result["passed"] is True
        assert result["score"] == 1.0

    def test_execute_fail_assertion(self, sandbox):
        result = sandbox.execute_python("assert 1 + 1 == 3")
        assert result["passed"] is False
        assert result["score"] == 0.0

    def test_execute_syntax_error(self, sandbox):
        result = sandbox.execute_python("this is not python")
        assert result["passed"] is False
        assert "error" in result

    def test_execute_timeout(self, sandbox):
        sandbox.timeout = 1
        result = sandbox.execute_python("import time; time.sleep(10)")
        assert result["passed"] is False
        assert "Timeout" in result["error"]

    def test_execute_with_variables(self, sandbox):
        result = sandbox.execute_python(
            "assert x == 42",
            variables={"x": 42},
        )
        assert result["passed"] is True

    def test_execute_with_variables_fail(self, sandbox):
        result = sandbox.execute_python(
            "assert x == 99",
            variables={"x": 42},
        )
        assert result["passed"] is False

    def test_execute_multiline_code(self, sandbox):
        code = """
import json
data = {"a": 1, "b": 2}
assert data["a"] + data["b"] == 3
"""
        result = sandbox.execute_python(code)
        assert result["passed"] is True

    def test_execute_with_output(self, sandbox):
        code = """
result = sum(range(10))
assert result == 45
"""
        result = sandbox.execute_python(code)
        assert result["passed"] is True

    def test_indent_method(self, sandbox):
        code = "line1\nline2\nline3"
        indented = sandbox._indent(code, 4)
        for line in indented.split("\n"):
            assert line.startswith("    ")
