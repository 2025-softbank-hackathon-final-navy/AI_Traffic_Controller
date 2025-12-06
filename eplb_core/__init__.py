"""
EPLB Core Module - Expert Parallelism Load Balancer

DeepSeek EPLB 알고리즘 원본 + 래퍼 API
"""

from .eplb import rebalance_experts
from .engine import compute_expert_assignment

__all__ = ['rebalance_experts', 'compute_expert_assignment']

