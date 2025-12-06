"""
Prewarm Controller Module

메인 스케줄러 + A/B 로직
"""

import time
import threading
import zlib
from typing import Dict, Optional, Callable
from dataclasses import dataclass

from core.redis import get_redis, RedisKeys
from .metrics_reader import collect_qps, update_ema
from .eplb_adapter import compute_desired_replicas
from .plan_store import save_plan_to_redis


@dataclass
class PrewarmConfig:
    """Prewarm 설정"""
    mode: str  # "off" | "on" | "experiment"
    alpha: float  # EMA 계수
    interval: int  # 루프 주기 (초)
    replicas: int  # 전체 replica 수
    aggressiveness: float = 1.0  # 전역 공격성


# 전역 컨트롤러 상태
_controller_thread: Optional[threading.Thread] = None
_controller_running: bool = False
_on_plan_computed: Optional[Callable[[Dict[str, int]], None]] = None


def load_global_config() -> PrewarmConfig:
    """
    Redis에서 글로벌 설정 로드
    
    Redis Keys:
        config:prewarm:mode              # "off" | "on" | "experiment"
        config:prewarm:global_replicas   # int
        config:prewarm:ema_alpha         # float
        config:prewarm:interval_sec      # int
    """
    redis = get_redis()
    
    mode = redis.get(RedisKeys.CONFIG_PREWARM_MODE) or "off"
    alpha_str = redis.get(RedisKeys.CONFIG_PREWARM_EMA_ALPHA)
    interval_str = redis.get(RedisKeys.CONFIG_PREWARM_INTERVAL)
    replicas_str = redis.get(RedisKeys.CONFIG_PREWARM_REPLICAS)
    
    return PrewarmConfig(
        mode=mode,
        alpha=float(alpha_str) if alpha_str else 0.3,
        interval=int(interval_str) if interval_str else 60,
        replicas=int(replicas_str) if replicas_str else 10,
    )


def save_global_config(config: PrewarmConfig) -> None:
    """
    글로벌 설정을 Redis에 저장
    """
    redis = get_redis()
    
    redis.set(RedisKeys.CONFIG_PREWARM_MODE, config.mode)
    redis.set(RedisKeys.CONFIG_PREWARM_EMA_ALPHA, str(config.alpha))
    redis.set(RedisKeys.CONFIG_PREWARM_INTERVAL, str(config.interval))
    redis.set(RedisKeys.CONFIG_PREWARM_REPLICAS, str(config.replicas))


def get_workspace_mode(workspace_id: str) -> Optional[str]:
    """
    workspace별 prewarm_mode 조회
    없으면 None 반환 (global 설정 사용)
    """
    redis = get_redis()
    key = RedisKeys.workspace_mode_key(workspace_id)
    return redis.get(key)


def set_workspace_mode(workspace_id: str, mode: str) -> None:
    """
    workspace별 prewarm_mode 설정
    """
    redis = get_redis()
    key = RedisKeys.workspace_mode_key(workspace_id)
    redis.set(key, mode)


def pick_bucket(workspace_id: str, ratio: float = 0.5) -> str:
    """
    workspace_id를 해시해서 control/treatment 결정 (Deterministic Hash)
    
    Parameters:
        workspace_id: workspace ID
        ratio: treatment 비율 (0.5 = 50%)
        
    Returns:
        "treatment" 또는 "control"
    """
    h = zlib.crc32(workspace_id.encode("utf-8")) % 1000  # 0~999
    threshold = int(1000 * ratio)
    return "treatment" if h < threshold else "control"


def get_effective_mode(workspace_id: Optional[str], global_mode: str) -> str:
    """
    workspace_id에 대한 실제 적용 모드 결정
    
    우선순위:
    1. workspace 별 override
    2. global 설정
    """
    if workspace_id:
        ws_mode = get_workspace_mode(workspace_id)
        if ws_mode:
            return ws_mode
    return global_mode


def should_apply_prewarm(
    workspace_id: Optional[str],
    global_mode: str,
) -> tuple[bool, Optional[str]]:
    """
    Prewarm 적용 여부 및 실험 버킷 결정
    
    Returns:
        (prewarm_enabled, experiment_bucket)
        - prewarm_enabled: Prewarm 적용 여부
        - experiment_bucket: "control" | "treatment" | None
    """
    mode = get_effective_mode(workspace_id, global_mode)
    
    if mode == "off":
        return False, None
    elif mode == "on":
        return True, None
    elif mode == "experiment":
        if workspace_id:
            bucket = pick_bucket(workspace_id)
            return bucket == "treatment", bucket
        else:
            # workspace_id가 없으면 treatment로 간주
            return True, "treatment"
    else:
        return False, None


def run_scheduler_loop():
    """
    메인 스케줄러 루프
    
    주기(기본 60초)마다:
    1. QPS 수집
    2. EMA 업데이트  
    3. EPLB로 replica 계획 계산
    4. plan Redis 저장
    """
    global _controller_running
    
    print("[Prewarm] Scheduler loop started")
    
    while _controller_running:
        try:
            cfg = load_global_config()
            
            if cfg.mode == "off":
                # 완전 비활성
                time.sleep(cfg.interval)
                continue
            
            # 1. QPS 수집
            qps_map = collect_qps(window_seconds=cfg.interval)
            
            # 2. EMA 업데이트
            ema_qps_map = update_ema(qps_map, alpha=cfg.alpha)
            
            if not ema_qps_map:
                print("[Prewarm] No active functions, skipping plan computation")
                time.sleep(cfg.interval)
                continue
            
            # 3. EPLB로 replica 계획 계산 (모드에 상관없이 전체 계획은 계산)
            base_plan = compute_desired_replicas(
                ema_qps_map,
                num_replicas=cfg.replicas,
                global_aggressiveness=cfg.aggressiveness,
            )
            
            # 4. plan Redis 저장
            save_plan_to_redis(base_plan)
            
            print(f"[Prewarm] Plan updated (mode={cfg.mode}): {len(base_plan)} functions")
            
            # 콜백 호출 (K8s applier 등)
            if _on_plan_computed:
                try:
                    _on_plan_computed(base_plan)
                except Exception as cb_error:
                    print(f"[Prewarm] Callback error: {cb_error}")
            
        except Exception as e:
            print(f"[Prewarm] Scheduler error: {e}")
        
        # 다음 루프까지 대기
        try:
            cfg = load_global_config()
            time.sleep(cfg.interval)
        except:
            time.sleep(60)  # fallback


def start_prewarm_controller(
    on_plan_computed: Optional[Callable[[Dict[str, int]], None]] = None
) -> None:
    """
    Prewarm Controller 시작
    
    Parameters:
        on_plan_computed: plan 계산 완료 시 호출할 콜백 (예: K8s applier)
    """
    global _controller_thread, _controller_running, _on_plan_computed
    
    if _controller_running:
        print("[Prewarm] Controller already running")
        return
    
    _on_plan_computed = on_plan_computed
    _controller_running = True
    
    _controller_thread = threading.Thread(
        target=run_scheduler_loop,
        daemon=True,
        name="PrewarmController"
    )
    _controller_thread.start()
    
    print("[Prewarm] Controller started")


def stop_prewarm_controller() -> None:
    """
    Prewarm Controller 중지
    """
    global _controller_thread, _controller_running
    
    if not _controller_running:
        print("[Prewarm] Controller not running")
        return
    
    _controller_running = False
    
    if _controller_thread and _controller_thread.is_alive():
        _controller_thread.join(timeout=5)
    
    _controller_thread = None
    print("[Prewarm] Controller stopped")


def is_controller_running() -> bool:
    """
    Controller 실행 상태 확인
    """
    return _controller_running


def get_controller_status() -> Dict[str, any]:
    """
    Controller 상태 정보 반환
    """
    config = load_global_config()
    return {
        "running": _controller_running,
        "mode": config.mode,
        "interval_sec": config.interval,
        "replicas": config.replicas,
        "ema_alpha": config.alpha,
    }


__all__ = [
    'PrewarmConfig',
    'load_global_config',
    'save_global_config',
    'get_workspace_mode',
    'set_workspace_mode',
    'pick_bucket',
    'get_effective_mode',
    'should_apply_prewarm',
    'start_prewarm_controller',
    'stop_prewarm_controller',
    'is_controller_running',
    'get_controller_status',
]

