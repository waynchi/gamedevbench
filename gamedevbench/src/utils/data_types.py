#!/usr/bin/env python3

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


# Token pricing per 1M tokens (USD) - Updated December 2024
TOKEN_PRICING = {
    # Anthropic Claude
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    # OpenAI GPT
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "o1": {"input": 15.00, "output": 60.00},
    "o1-mini": {"input": 3.00, "output": 12.00},
    # OpenAI Codex (uses GPT-4o pricing as base)
    "codex": {"input": 2.50, "output": 10.00},
    # Google Gemini CLI (free)
    "gemini": {"input": 0.00, "output": 0.00},
    # Default fallback
    "default": {"input": 3.00, "output": 15.00},
}


@dataclass
class TokenUsage:
    """Container for token usage statistics."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def calculate_cost(self, model: str) -> float:
        """Calculate cost in USD based on model pricing."""
        # Normalize model name
        model_lower = model.lower()
        pricing = TOKEN_PRICING.get("default")

        for key in TOKEN_PRICING:
            if key in model_lower:
                pricing = TOKEN_PRICING[key]
                break

        input_cost = (self.input_tokens / 1_000_000) * pricing["input"]
        output_cost = (self.output_tokens / 1_000_000) * pricing["output"]

        return input_cost + output_cost

    def to_dict(self) -> Dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


@dataclass
class ValidationResult:
    """Container for validation test results."""

    success: bool
    message: str
    details: Optional[Dict] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }

    def __str__(self) -> str:
        status = "PASSED" if self.success else "FAILED"
        return f"{status}: {self.message}"


@dataclass
class SolverResult:
    """Container for solver results with token usage tracking."""

    success: bool
    message: str
    duration_seconds: float
    stdout: str = ""
    stderr: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    is_rate_limited: bool = False  # Flag for API quota/rate limit errors
    token_usage: Optional[TokenUsage] = None  # Token usage statistics
    model: str = ""  # Model used for this run
    cost_usd: float = 0.0  # Calculated cost in USD

    def calculate_cost(self) -> float:
        """Calculate and store the cost based on token usage."""
        if self.token_usage and self.model:
            self.cost_usd = self.token_usage.calculate_cost(self.model)
        return self.cost_usd

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "success": self.success,
            "message": self.message,
            "duration_seconds": self.duration_seconds,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timestamp": self.timestamp,
            "is_rate_limited": self.is_rate_limited,
            "model": self.model,
            "cost_usd": self.cost_usd,
        }
        if self.token_usage:
            result["token_usage"] = self.token_usage.to_dict()
        return result

    def __str__(self) -> str:
        status = "COMPLETED" if self.success else "FAILED"
        token_info = ""
        if self.token_usage:
            token_info = f", tokens: {self.token_usage.total_tokens}, cost: ${self.cost_usd:.4f}"
        return f"{status}: {self.message} (took {self.duration_seconds:.2f}s{token_info})"
