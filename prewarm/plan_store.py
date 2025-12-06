"""
Plan Store Module

EPLB 결과 plan을 Redis에 저장/조회
"""

import re
import json
from typing import Dict, Optional
from datetime import datetime

from core.redis import get_redis, RedisKeys


def save_plan_to_redis(
    plan: Dict[str, int],
    ttl_seconds: int = 300,
) -> None:
    """
    EPLB 결과 plan(function_id -> replicas)을 Redis에 저장.
    
    Parameters:
        plan: function_id -> desired_replicas 맵
        ttl_seconds: TTL (기본 5분)
        
    Redis Key:
        plan:function:{function_id}:desired_replicas
    """
    redis = get_redis()
    
    for function_id, replicas in plan.items():
        key = RedisKeys.plan_replicas_key(function_id)
        redis.setex(key, ttl_seconds, str(replicas))
    
    # 메타데이터 저장 (마지막 업데이트 시간)
    redis.set("plan:meta:last_updated", datetime.now().isoformat())
    redis.set("plan:meta:function_count", str(len(plan)))


def load_plan_from_redis() -> Dict[str, int]:
    """
    현재 Redis에 저장된 plan:* 키들로부터 plan 맵을 복원.
    
    Returns:
        function_id -> desired_replicas 맵
    """
    redis = get_redis()
    plan: Dict[str, int] = {}
    
    # SCAN으로 plan 키 검색
    cursor = 0
    while True:
        cursor, keys = redis.scan(
            cursor=cursor, 
            match="plan:function:*:desired_replicas", 
            count=100
        )
        
        for key in keys:
            # 키에서 function_id 추출
            match = re.match(r"plan:function:(.+):desired_replicas", key)
            if match:
                function_id = match.group(1)
                try:
                    replicas = int(redis.get(key) or 0)
                    plan[function_id] = replicas
                except (ValueError, TypeError):
                    continue
        
        if cursor == 0:
            break
    
    return plan


def get_plan_for_function(function_id: str) -> Optional[int]:
    """
    특정 함수의 desired_replicas 조회
    
    Parameters:
        function_id: 함수 ID
        
    Returns:
        desired_replicas 또는 None (없는 경우)
    """
    redis = get_redis()
    key = RedisKeys.plan_replicas_key(function_id)
    
    value = redis.get(key)
    if value is not None:
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
    return None


def delete_plan_for_function(function_id: str) -> bool:
    """
    특정 함수의 plan 삭제
    
    Returns:
        삭제 성공 여부
    """
    redis = get_redis()
    key = RedisKeys.plan_replicas_key(function_id)
    return redis.delete(key) > 0


def clear_all_plans() -> int:
    """
    모든 plan 삭제
    
    Returns:
        삭제된 키 수
    """
    redis = get_redis()
    deleted_count = 0
    
    cursor = 0
    while True:
        cursor, keys = redis.scan(
            cursor=cursor, 
            match="plan:function:*:desired_replicas", 
            count=100
        )
        
        if keys:
            deleted_count += redis.delete(*keys)
        
        if cursor == 0:
            break
    
    # 메타데이터도 삭제
    redis.delete("plan:meta:last_updated", "plan:meta:function_count")
    
    return deleted_count


def get_plan_metadata() -> Dict[str, any]:
    """
    Plan 메타데이터 조회
    
    Returns:
        메타데이터 딕셔너리
    """
    redis = get_redis()
    
    last_updated = redis.get("plan:meta:last_updated")
    function_count = redis.get("plan:meta:function_count")
    
    return {
        "last_updated": last_updated,
        "function_count": int(function_count) if function_count else 0,
    }


def save_plan_snapshot(plan: Dict[str, int], snapshot_id: Optional[str] = None) -> str:
    """
    Plan 스냅샷 저장 (히스토리 용도)
    
    Parameters:
        plan: function_id -> desired_replicas 맵
        snapshot_id: 스냅샷 ID (없으면 자동 생성)
        
    Returns:
        스냅샷 ID
    """
    redis = get_redis()
    
    if snapshot_id is None:
        snapshot_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    snapshot_key = f"plan:snapshot:{snapshot_id}"
    redis.setex(
        snapshot_key,
        3600,  # 1시간 TTL
        json.dumps({
            "plan": plan,
            "timestamp": datetime.now().isoformat(),
        })
    )
    
    return snapshot_id


def load_plan_snapshot(snapshot_id: str) -> Optional[Dict[str, int]]:
    """
    Plan 스냅샷 로드
    
    Parameters:
        snapshot_id: 스냅샷 ID
        
    Returns:
        plan 맵 또는 None
    """
    redis = get_redis()
    
    snapshot_key = f"plan:snapshot:{snapshot_id}"
    data = redis.get(snapshot_key)
    
    if data:
        try:
            parsed = json.loads(data)
            return parsed.get("plan", {})
        except (json.JSONDecodeError, TypeError):
            pass
    
    return None


__all__ = [
    'save_plan_to_redis',
    'load_plan_from_redis',
    'get_plan_for_function',
    'delete_plan_for_function',
    'clear_all_plans',
    'get_plan_metadata',
    'save_plan_snapshot',
    'load_plan_snapshot',
]

