"""
Prewarm Module - Prewarm Controller + EPLB Integration

V2 실전용 스펙 기준:
- metrics_reader: Redis에서 QPS/EMA 수집/갱신
- eplb_adapter: Prewarm 전용 어댑터 (EMA → weight → engine 호출)
- plan_store: 계획 Redis 저장/조회
- controller: 메인 스케줄러 + A/B 로직
- k8s_applier: (선택) K8s/인프라에 replica 적용
"""

from .controller import start_prewarm_controller, stop_prewarm_controller
from .metrics_reader import collect_qps, update_ema
from .eplb_adapter import compute_desired_replicas, get_time_coefficient
from .plan_store import save_plan_to_redis, load_plan_from_redis

__all__ = [
    'start_prewarm_controller',
    'stop_prewarm_controller',
    'collect_qps',
    'update_ema',
    'compute_desired_replicas',
    'get_time_coefficient',
    'save_plan_to_redis',
    'load_plan_from_redis',
]

