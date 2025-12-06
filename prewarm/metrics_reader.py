"""
Metrics Reader Module

Redis에서 QPS/EMA 수집 및 갱신
"""

import time
import re
from typing import Dict, Optional

from core.redis import get_redis, RedisKeys


def collect_qps(window_seconds: int = 60) -> Dict[str, float]:
    """
    지난 window_seconds 동안의 실행 로그를 기준으로
    function_id -> QPS 의 맵을 계산한다.
    
    - 현재 시각 기준 직전 1분만 사용 (간단 버전)
    - Redis 키: metrics:function:{func_id}:calls:{epoch_minute}
    
    Parameters:
        window_seconds: 윈도우 크기 (기본 60초)
    
    Returns:
        function_id -> QPS 맵
    """
    redis = get_redis()
    qps_map: Dict[str, float] = {}
    
    # 현재 시각 기준 직전 1분
    now_minute = int(time.time() // 60)
    target_minute = now_minute - 1
    
    # 패턴으로 키 검색
    pattern = f"metrics:function:*:calls:{target_minute}"
    
    # SCAN으로 키 목록 가져오기
    cursor = 0
    keys = []
    while True:
        cursor, batch = redis.scan(cursor=cursor, match=pattern, count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    
    # 각 키에서 function_id 추출 및 QPS 계산
    # 키 형식: metrics:function:{function_id}:calls:{epoch_minute}
    key_pattern = re.compile(r"metrics:function:(.+):calls:\d+")
    
    for key in keys:
        match = key_pattern.match(key)
        if match:
            function_id = match.group(1)
            try:
                calls = int(redis.get(key) or 0)
                qps = calls / window_seconds
                qps_map[function_id] = qps
            except (ValueError, TypeError):
                continue
    
    return qps_map


def update_ema(
    qps_map: Dict[str, float],
    alpha: float = 0.3
) -> Dict[str, float]:
    """
    function_id -> QPS 맵과 EMA 계수 alpha를 받아,
    EMA를 갱신하고 최신 ema_qps_map 반환.
    
    EMA 공식: EMA_new = alpha * QPS_current + (1 - alpha) * EMA_old
    
    Parameters:
        qps_map: function_id -> 현재 QPS 맵
        alpha: EMA 계수 (0 < alpha <= 1), 높을수록 최근 값에 가중치
    
    Returns:
        function_id -> EMA QPS 맵
        
    Redis:
        state:function:{function_id}:ema_qps
    """
    redis = get_redis()
    ema_qps_map: Dict[str, float] = {}
    
    for function_id, current_qps in qps_map.items():
        ema_key = RedisKeys.state_ema_key(function_id)
        
        # 기존 EMA 값 가져오기
        old_ema_str = redis.get(ema_key)
        
        if old_ema_str is not None:
            try:
                old_ema = float(old_ema_str)
                # EMA 계산
                new_ema = alpha * current_qps + (1 - alpha) * old_ema
            except (ValueError, TypeError):
                new_ema = current_qps
        else:
            # 첫 번째 값은 그대로 사용
            new_ema = current_qps
        
        # Redis에 저장
        redis.set(ema_key, str(new_ema))
        ema_qps_map[function_id] = new_ema
    
    # 기존에 있던 EMA 값 중 현재 QPS에 없는 것들도 포함 (decay)
    # 활동이 없는 함수는 EMA를 감쇠시킴
    existing_ema_keys = []
    cursor = 0
    while True:
        cursor, batch = redis.scan(cursor=cursor, match="state:function:*:ema_qps", count=100)
        existing_ema_keys.extend(batch)
        if cursor == 0:
            break
    
    ema_key_pattern = re.compile(r"state:function:(.+):ema_qps")
    for ema_key in existing_ema_keys:
        match = ema_key_pattern.match(ema_key)
        if match:
            function_id = match.group(1)
            if function_id not in qps_map:
                # QPS가 0인 경우 EMA 감쇠
                try:
                    old_ema = float(redis.get(ema_key) or 0)
                    new_ema = (1 - alpha) * old_ema
                    
                    # 너무 작은 값은 제거
                    if new_ema < 0.001:
                        redis.delete(ema_key)
                    else:
                        redis.set(ema_key, str(new_ema))
                        ema_qps_map[function_id] = new_ema
                except (ValueError, TypeError):
                    continue
    
    return ema_qps_map


def get_all_ema_qps() -> Dict[str, float]:
    """
    Redis에 저장된 모든 EMA QPS 값을 반환
    
    Returns:
        function_id -> EMA QPS 맵
    """
    redis = get_redis()
    ema_qps_map: Dict[str, float] = {}
    
    cursor = 0
    while True:
        cursor, keys = redis.scan(cursor=cursor, match="state:function:*:ema_qps", count=100)
        for key in keys:
            # 키에서 function_id 추출
            match = re.match(r"state:function:(.+):ema_qps", key)
            if match:
                function_id = match.group(1)
                try:
                    ema_value = float(redis.get(key) or 0)
                    ema_qps_map[function_id] = ema_value
                except (ValueError, TypeError):
                    continue
        if cursor == 0:
            break
    
    return ema_qps_map


def record_function_call(function_id: str) -> int:
    """
    함수 호출 기록 (Routing 서버에서 호출)
    
    Parameters:
        function_id: 함수 ID
        
    Returns:
        현재 분의 총 호출 수
    """
    redis = get_redis()
    
    # 현재 분
    current_minute = int(time.time() // 60)
    key = RedisKeys.metrics_calls_key(function_id, current_minute)
    
    # 카운터 증가
    count = redis.incr(key)
    
    # TTL 설정 (5분 후 자동 삭제)
    redis.expire(key, 300)
    
    return count


__all__ = [
    'collect_qps',
    'update_ema',
    'get_all_ema_qps',
    'record_function_call',
]

