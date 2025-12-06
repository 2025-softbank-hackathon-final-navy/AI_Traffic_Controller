"""
EPLB Adapter Module

Prewarm 전용 어댑터: EMA → weight → eplb_core.engine 호출
"""

from datetime import datetime
from typing import Dict, Optional, List

from eplb_core.engine import compute_expert_assignment


# 함수당 최대 replica 수
MAX_PER_FUNCTION = 20

# 최소 replica 수 (0이면 완전 비활성화 가능)
MIN_PER_FUNCTION = 0


def get_time_coefficient(hour: int) -> float:
    """
    0~23시 기준 시간대 계수.
    
    트래픽 패턴에 따라 시간대별로 다른 가중치 부여:
    - 심야 (0~5시): 낮은 트래픽 0.6 ?
    - 이른 아침 (6~8시): 증가 시작 0.9 ?
    - 업무 시간 (9~18시): 높은 트래픽 1.3 ?
    - 저녁 피크 (19~22시): 최고 트래픽 1.5 ?
    - 늦은 밤 (23시): 감소 시작 0.8 ?
    
    Parameters:
        hour: 0~23 시간
        
    Returns:
        시간대 계수 (0.6 ~ 1.5)
    """
    if 0 <= hour <= 5:
        return 0.6
    elif 6 <= hour <= 8:
        return 0.9
    elif 9 <= hour <= 18:
        return 1.3
    elif 19 <= hour <= 22:
        return 1.5
    else:  # 23시
        return 0.8


def get_current_time_coefficient() -> float:
    """
    현재 시간 기준 시간대 계수 반환
    """
    current_hour = datetime.now().hour
    return get_time_coefficient(current_hour)


def compute_desired_replicas(
    ema_qps_map: Dict[str, float],
    num_replicas: int,
    global_aggressiveness: float = 1.0,
    per_function_aggressiveness: Optional[Dict[str, float]] = None,
    apply_time_coefficient: bool = True,
) -> Dict[str, int]:
    """
    EMA(QPS) + 시간대 계수를 기반으로 weight를 만들고,
    eplb_core.engine을 통해 function_id -> desired_replicas를 계산한다.
    
    Parameters:
        ema_qps_map: function_id -> EMA QPS 맵
        num_replicas: 전체 할당 가능한 replica 수
        global_aggressiveness: 전역 공격성 계수 (기본 1.0)
        per_function_aggressiveness: 함수별 공격성 계수 (옵션)
        apply_time_coefficient: 시간대 계수 적용 여부
    
    Returns:
        function_id -> desired_replicas 맵
        
    로직:
        1. func_ids = list(ema_qps_map.keys())
        2. k_hour = get_time_coefficient(현재시간) if apply_time_coefficient else 1.0
        3. 각 func_id에 대해:
           A_f = per_function_aggressiveness.get(func_id, 1.0)
           weight_f = max(0.0, ema_qps * k_hour * global_aggressiveness * A_f)
        4. weights → EPLB engine → replica_counts
        5. clamp(replica_counts[i], MIN_PER_FUNCTION, MAX_PER_FUNCTION)
    """
    if not ema_qps_map:
        return {}
    
    if num_replicas <= 0:
        return {func_id: 0 for func_id in ema_qps_map}
    
    func_ids = list(ema_qps_map.keys())
    
    # 시간대 계수
    k_hour = get_current_time_coefficient() if apply_time_coefficient else 1.0
    
    # 가중치 계산
    weights: List[float] = []
    for func_id in func_ids:
        ema_qps = ema_qps_map[func_id]
        
        # 함수별 공격성 계수
        A_f = 1.0
        if per_function_aggressiveness:
            A_f = per_function_aggressiveness.get(func_id, 1.0)
        
        # 최종 가중치
        weight_f = max(0.0, ema_qps * k_hour * global_aggressiveness * A_f)
        weights.append(weight_f)
    
    # EPLB engine 호출
    replica_counts = compute_expert_assignment(weights, num_replicas)
    
    # 결과 맵 생성 (clamp 적용)
    desired_replicas: Dict[str, int] = {}
    for i, func_id in enumerate(func_ids):
        count = replica_counts[i] if i < len(replica_counts) else 0
        # clamp
        count = max(MIN_PER_FUNCTION, min(count, MAX_PER_FUNCTION))
        desired_replicas[func_id] = count
    
    return desired_replicas


def compute_weight_distribution(
    ema_qps_map: Dict[str, float],
    global_aggressiveness: float = 1.0,
    per_function_aggressiveness: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    각 함수의 최종 가중치를 계산하여 반환 (디버깅/모니터링용)
    
    Returns:
        function_id -> weight 맵
    """
    if not ema_qps_map:
        return {}
    
    k_hour = get_current_time_coefficient()
    
    weight_map: Dict[str, float] = {}
    for func_id, ema_qps in ema_qps_map.items():
        A_f = 1.0
        if per_function_aggressiveness:
            A_f = per_function_aggressiveness.get(func_id, 1.0)
        
        weight_f = max(0.0, ema_qps * k_hour * global_aggressiveness * A_f)
        weight_map[func_id] = weight_f
    
    return weight_map


def get_time_info() -> Dict[str, any]:
    """
    현재 시간 정보 반환 (디버깅용)
    """
    now = datetime.now()
    return {
        "current_hour": now.hour,
        "current_minute": now.minute,
        "time_coefficient": get_time_coefficient(now.hour),
        "timestamp": now.isoformat(),
    }


__all__ = [
    'get_time_coefficient',
    'get_current_time_coefficient',
    'compute_desired_replicas',
    'compute_weight_distribution',
    'get_time_info',
    'MAX_PER_FUNCTION',
    'MIN_PER_FUNCTION',
]

