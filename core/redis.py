"""
Redis Client Module

Redis 연결 및 공통 유틸리티 제공
"""

import os
from typing import Optional, Any
import redis
from redis import Redis


# 환경변수에서 Redis 설정 로드
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# 싱글톤 Redis 클라이언트
_redis_client: Optional[Redis] = None


def get_redis() -> Redis:
    """
    Redis 클라이언트 싱글톤 반환
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            decode_responses=True,  # 문자열로 자동 디코딩
            socket_timeout=5,
            socket_connect_timeout=5,
        )
    return _redis_client


def close_redis() -> None:
    """
    Redis 연결 종료
    """
    global _redis_client
    if _redis_client is not None:
        _redis_client.close()
        _redis_client = None


class RedisClient:
    """
    Redis 클라이언트 래퍼 클래스
    
    컨텍스트 매니저 및 유틸리티 메서드 제공
    """
    
    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        db: int = REDIS_DB,
        password: Optional[str] = REDIS_PASSWORD,
    ):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self._client: Optional[Redis] = None
    
    def __enter__(self) -> Redis:
        self._client = redis.Redis(
            host=self.host,
            port=self.port,
            db=self.db,
            password=self.password,
            decode_responses=True,
        )
        return self._client
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            self._client.close()
            self._client = None
    
    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=True,
            )
        return self._client
    
    def get_or_default(self, key: str, default: Any = None) -> Any:
        """
        키 값을 가져오거나 기본값 반환
        """
        value = self.client.get(key)
        return value if value is not None else default
    
    def set_with_ttl(self, key: str, value: Any, ttl_seconds: int) -> bool:
        """
        TTL과 함께 값 설정
        """
        return self.client.setex(key, ttl_seconds, value)
    
    def increment(self, key: str, amount: int = 1) -> int:
        """
        카운터 증가
        """
        return self.client.incrby(key, amount)
    
    def scan_keys(self, pattern: str) -> list:
        """
        패턴에 맞는 키 목록 반환 (SCAN 사용)
        """
        keys = []
        cursor = 0
        while True:
            cursor, batch = self.client.scan(cursor=cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        return keys


# Redis Key Prefixes (SPEC.md 기준)
class RedisKeys:
    """
    Redis 키 패턴 상수
    """
    # 메트릭
    METRICS_FUNCTION_CALLS = "metrics:function:{function_id}:calls:{epoch_minute}"
    
    # EMA 상태
    STATE_FUNCTION_EMA = "state:function:{function_id}:ema_qps"
    
    # Prewarm 계획
    PLAN_FUNCTION_REPLICAS = "plan:function:{function_id}:desired_replicas"
    
    # 전역 설정
    CONFIG_PREWARM_MODE = "config:prewarm:mode"
    CONFIG_PREWARM_REPLICAS = "config:prewarm:global_replicas"
    CONFIG_PREWARM_EMA_ALPHA = "config:prewarm:ema_alpha"
    CONFIG_PREWARM_INTERVAL = "config:prewarm:interval_sec"
    
    # Workspace 설정
    CONFIG_WORKSPACE_MODE = "config:workspace:{workspace_id}:prewarm_mode"
    CONFIG_WORKSPACE_AGGRESSIVENESS = "config:workspace:{workspace_id}:aggressiveness"
    
    @staticmethod
    def metrics_calls_key(function_id: str, epoch_minute: int) -> str:
        return f"metrics:function:{function_id}:calls:{epoch_minute}"
    
    @staticmethod
    def state_ema_key(function_id: str) -> str:
        return f"state:function:{function_id}:ema_qps"
    
    @staticmethod
    def plan_replicas_key(function_id: str) -> str:
        return f"plan:function:{function_id}:desired_replicas"
    
    @staticmethod
    def workspace_mode_key(workspace_id: str) -> str:
        return f"config:workspace:{workspace_id}:prewarm_mode"
    
    @staticmethod
    def workspace_aggressiveness_key(workspace_id: str) -> str:
        return f"config:workspace:{workspace_id}:aggressiveness"


__all__ = ['get_redis', 'close_redis', 'RedisClient', 'RedisKeys']

