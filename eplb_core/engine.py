"""
EPLB Engine - 래퍼 API

DeepSeek EPLB의 rebalance_experts를 감싸는 고수준 API 제공
"""

from typing import List, Tuple, Optional
import torch

from .eplb import rebalance_experts


def compute_expert_assignment(
    weights: List[float],
    num_replicas: int,
    num_groups: int = 1,
    num_nodes: int = 1
) -> List[int]:
    """
    가중치 기반으로 각 expert(함수)에 할당할 replica 수를 계산한다.

    Parameters:
        weights: 각 expert(함수)의 가중치 리스트 (예: EMA QPS 기반)
        num_replicas: 전체 할당 가능한 replica 수
        num_groups: expert 그룹 수 (기본 1)
        num_nodes: 서버 노드 수 (기본 1)

    Returns:
        각 expert에 할당된 replica 수 리스트

    Example:
        >>> weights = [10.0, 5.0, 3.0, 2.0]  # 4개 함수의 가중치
        >>> num_replicas = 8
        >>> result = compute_expert_assignment(weights, num_replicas)
        >>> print(result)  # [3, 2, 2, 1] 같은 형태
    """
    if not weights:
        return []
    
    num_experts = len(weights)
    
    # num_replicas가 num_experts보다 작으면 조정
    if num_replicas < num_experts:
        # 각 expert에 최소 0개, 가중치 높은 순서대로 할당
        sorted_indices = sorted(range(num_experts), key=lambda i: weights[i], reverse=True)
        result = [0] * num_experts
        for i in range(num_replicas):
            result[sorted_indices[i]] = 1
        return result
    
    # EPLB는 num_replicas가 num_gpus의 배수여야 함
    # 단순화를 위해 num_gpus = num_replicas로 설정 (각 replica가 하나의 GPU처럼 동작)
    num_gpus = num_replicas
    
    # weight tensor 생성: [layers=1, num_logical_experts]
    weight_tensor = torch.tensor([weights], dtype=torch.float32)
    
    # 모든 가중치가 0인 경우 균등 분배
    if weight_tensor.sum() == 0:
        base = num_replicas // num_experts
        remainder = num_replicas % num_experts
        result = [base] * num_experts
        for i in range(remainder):
            result[i] += 1
        return result
    
    try:
        phy2log, log2phy, logcnt = rebalance_experts(
            weight_tensor,
            num_replicas=num_replicas,
            num_groups=num_groups,
            num_nodes=num_nodes,
            num_gpus=num_gpus
        )
        # logcnt: [layers=1, num_experts] → [num_experts]
        return logcnt[0].tolist()
    except AssertionError:
        # EPLB 제약조건 불만족 시 fallback: 가중치 비례 분배
        return _fallback_proportional_assignment(weights, num_replicas)


def _fallback_proportional_assignment(weights: List[float], num_replicas: int) -> List[int]:
    """
    EPLB 사용 불가 시 가중치 비례 분배 (fallback)
    """
    total_weight = sum(weights)
    if total_weight == 0:
        # 균등 분배
        base = num_replicas // len(weights)
        remainder = num_replicas % len(weights)
        result = [base] * len(weights)
        for i in range(remainder):
            result[i] += 1
        return result
    
    # 가중치 비례로 분배
    result = []
    remaining = num_replicas
    
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            # 마지막은 남은 것 모두 할당
            result.append(remaining)
        else:
            share = int(num_replicas * (w / total_weight))
            share = max(0, min(share, remaining))
            result.append(share)
            remaining -= share
    
    return result


def get_assignment_details(
    weights: List[float],
    num_replicas: int,
    num_groups: int = 1,
    num_nodes: int = 1
) -> Tuple[List[int], List[int], List[int]]:
    """
    상세 할당 정보를 반환한다.

    Returns:
        Tuple of:
            - phy2log: physical replica → logical expert 매핑
            - log2phy: logical expert → physical replica 매핑 (첫 번째만)
            - logcnt: 각 logical expert의 replica 수
    """
    if not weights:
        return [], [], []
    
    num_experts = len(weights)
    num_gpus = num_replicas
    
    weight_tensor = torch.tensor([weights], dtype=torch.float32)
    
    if weight_tensor.sum() == 0 or num_replicas < num_experts:
        # fallback
        logcnt = _fallback_proportional_assignment(weights, num_replicas)
        phy2log = []
        for i, cnt in enumerate(logcnt):
            phy2log.extend([i] * cnt)
        log2phy = []
        idx = 0
        for cnt in logcnt:
            log2phy.append(idx if cnt > 0 else -1)
            idx += cnt
        return phy2log, log2phy, logcnt
    
    try:
        phy2log_t, log2phy_t, logcnt_t = rebalance_experts(
            weight_tensor,
            num_replicas=num_replicas,
            num_groups=num_groups,
            num_nodes=num_nodes,
            num_gpus=num_gpus
        )
        return (
            phy2log_t[0].tolist(),
            log2phy_t[0, :, 0].tolist(),  # 첫 번째 replica index만
            logcnt_t[0].tolist()
        )
    except AssertionError:
        logcnt = _fallback_proportional_assignment(weights, num_replicas)
        phy2log = []
        for i, cnt in enumerate(logcnt):
            phy2log.extend([i] * cnt)
        log2phy = []
        idx = 0
        for cnt in logcnt:
            log2phy.append(idx if cnt > 0 else -1)
            idx += cnt
        return phy2log, log2phy, logcnt


__all__ = ['compute_expert_assignment', 'get_assignment_details']

