"""
Math tool with safe expression evaluation.

Supports complex mathematical expressions with parentheses and operator precedence.
"""

from __future__ import annotations

import ast
from typing import Any

name: str = "math"

# User-facing description (shown in CLI tool list)
display_description: str = (
    "Evaluate mathematical expressions with support for complex operations, "
    "parentheses, and operator precedence."
)

# LLM instruction description (includes warnings for model)
# Trimmed 2026-09-18 (adding a 4th tool, find_nearby, pushed the small
# on-device model's total system-prompt token count over its hard 2048-token
# context limit -- see magi-local-llm-feasibility.md Session 12's identical
# finding when `directions` was added as the 3rd tool. This block used to
# also spell out the exact <tool_call>{...}</tool_call> JSON format and
# worked examples, which is pure duplication: system_prompt.py's own
# generic "# How to Call a Tool" section (shared across every tool, appended
# once regardless of how many tools are registered) already states the same
# format+rules for the whole tool set. Removing the duplicate block here
# frees real token budget without losing any instruction the model doesn't
# already see elsewhere. The CRITICAL RULE / DEFAULT OPTION guidance below,
# which IS math-tool-specific, is kept.)
description: str = (
    "CRITICAL RULE - YOU MUST FOLLOW THIS: You MUST use this tool for ALL arithmetic "
    "operations, NO EXCEPTIONS. NEVER calculate math directly in your response - ALWAYS "
    "call this tool first. Even for simple calculations like '2 + 2' or '5 * 3', you MUST "
    "call this tool. The function name is 'math'. "
    "Pass any mathematical expression as a string in the 'expression' parameter. "
    "Supports: addition (+), subtraction (-), multiplication (*), division (/), "
    "floor division (//), modulo (%), power (**), parentheses, and negative numbers.\n\n"
    "DEFAULT OPTION: If the user requests an unknown or unsupported operation (e.g., trigonometric functions, "
    "logarithms, or operations not supported by this tool), set 'default' to true. "
    "Use this when you cannot translate the user's request into a valid mathematical expression. "
    "The tool will automatically generate an appropriate error message."
)

# Minimal JSON-like schema to assist prompting/validation
schema: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expression": {
            "type": "string",
            "description": (
                "Mathematical expression to evaluate. "
                "Examples: '5 + 3', '2 - 3 * (2 + 3) / 2', '(10 + 5) * 2 - 8 / 4'. "
                "Supports: +, -, *, /, //, %, **, parentheses, and negative numbers. "
                "Required unless 'default' is used."
            ),
        },
        "default": {
            "type": "boolean",
            "description": (
                "Set to true when the user requests an unknown or unsupported operation. "
                "Only use this if you cannot translate the user's request into a valid expression. "
                "The tool will automatically generate an appropriate error message."
            ),
        },
    },
    "required": [],
}

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        },
    }
]


class SafeExpressionEvaluator(ast.NodeVisitor):
    """
    Safe AST visitor that only allows mathematical operations.

    Only permits:
    - Numbers (ast.Constant)
    - Binary operations: +, -, *, /, //, %, **
    - Unary operations: +, -
    """

    def __init__(self):
        """Initialize with safe operation mappings."""
        self.safe_ops = {
            ast.Add: lambda a, b: a + b,
            ast.Sub: lambda a, b: a - b,
            ast.Mult: lambda a, b: a * b,
            ast.Div: lambda a, b: a / b,
            ast.FloorDiv: lambda a, b: a // b,
            ast.Mod: lambda a, b: a % b,
            ast.Pow: lambda a, b: a**b,
        }
        self.safe_unary_ops = {
            ast.UAdd: lambda a: +a,
            ast.USub: lambda a: -a,
        }

    def visit_Constant(self, node: ast.Constant) -> float:
        """
        Visit a constant (number) node.

        Args:
            node: AST constant node.

        Returns:
            The numeric value.

        Raises:
            ValueError: If constant is not a number.
        """
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Invalid constant type: {type(node.value).__name__}")

    def visit_BinOp(self, node: ast.BinOp) -> float:
        """
        Visit a binary operation node.

        Args:
            node: AST binary operation node.

        Returns:
            Result of the binary operation.

        Raises:
            ValueError: If operator is not allowed.
            ZeroDivisionError: If division by zero occurs.
        """
        if type(node.op) not in self.safe_ops:
            raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")

        left = self.visit(node.left)
        right = self.visit(node.right)

        # Check for division by zero
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError("Division by zero")

        return self.safe_ops[type(node.op)](left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float:
        """
        Visit a unary operation node.

        Args:
            node: AST unary operation node.

        Returns:
            Result of the unary operation.

        Raises:
            ValueError: If operator is not allowed.
        """
        if type(node.op) not in self.safe_unary_ops:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

        operand = self.visit(node.operand)
        return self.safe_unary_ops[type(node.op)](operand)

    def generic_visit(self, node: ast.AST) -> None:
        """
        Visit any node type not explicitly handled.

        Args:
            node: AST node.

        Raises:
            ValueError: For any unsupported node type.
        """
        raise ValueError(f"Unsupported expression component: {type(node).__name__}")


def _safe_evaluate_expression(expression: str) -> float:
    """
    Safely evaluate a mathematical expression string.

    Only allows safe operations: numbers, basic operators, parentheses, unary operators.
    Explicitly rejects: function calls, imports, variable access, attribute access.

    Args:
        expression: Mathematical expression as a string (e.g., "2 - 3 * (2 + 3) / 2")

    Returns:
        The evaluated result as a float.

    Raises:
        ValueError: If expression contains invalid syntax or unsupported operations.
        ZeroDivisionError: If division by zero occurs.
    """
    try:
        # Parse expression into AST
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e.msg}") from e

    # Evaluate using safe visitor
    evaluator = SafeExpressionEvaluator()
    try:
        result = evaluator.visit(tree.body)
        return float(result)
    except (ValueError, ZeroDivisionError):
        raise
    except Exception as e:
        raise ValueError(f"Error evaluating expression: {str(e)}") from e


def _validate_input(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Validate input payload.

    Args:
        payload: Dictionary with 'expression' key (or 'default').

    Returns:
        Dictionary with 'ok' and 'data' (if successful) or 'error' (if failed).
    """
    # If default is provided, expression is not required
    if payload.get("default") is True:
        return {"ok": True, "data": {"expression": None}}

    try:
        from pydantic import BaseModel, Field

        class MathInput(BaseModel):
            expression: str = Field(
                description="Mathematical expression to evaluate", min_length=1
            )

        data = MathInput(**payload).model_dump()
        return {"ok": True, "data": data}
    except Exception:
        # Fallback without pydantic
        try:
            expression = str(payload.get("expression", "")).strip()
            if not expression:
                return {
                    "ok": False,
                    "error": "Either 'expression' or 'default' must be provided",
                }
            return {"ok": True, "data": {"expression": expression}}
        except Exception as inner_exc:
            return {"ok": False, "error": f"Validation failed: {inner_exc}"}


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    """
    Execute the math expression evaluation.

    Args:
        input_data: Dictionary with keys:
            - expression: Mathematical expression string to evaluate (required if default not used).
            - default: Message explaining supported operations (use when request is unsupported).

    Returns:
        Dictionary with 'ok' and 'result' (if successful) or 'error' (if failed).
    """
    # Check for default option first (user error - agent correctly used default)
    if input_data.get("default") is True:
        return {
            "ok": True,  # Agent used tool correctly (default option)
            "error": f"I couldn't translate your question to the supported operations.",
        }

    validated = _validate_input(input_data)
    if not validated.get("ok"):
        return validated

    expression = validated["data"]["expression"]

    try:
        result = _safe_evaluate_expression(expression)

        # Format result in a user-friendly way
        # Check if result is a whole number (no decimal part)
        if result == int(result):
            result_str = str(int(result))
        else:
            # Format with appropriate precision (remove trailing zeros)
            result_str = f"{result:.10g}"

        formatted_result = f"{expression} = {result_str}"
        return {"ok": True, "result": formatted_result}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except ZeroDivisionError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"Unexpected error: {str(e)}"}

