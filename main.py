"""
Execution Server - Main Entry Point

FastAPI 서버 + Prewarm Controller 통합
"""

import os
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.redis import get_redis, close_redis, RedisKeys
from prewarm.controller import (
    start_prewarm_controller,
    stop_prewarm_controller,
    is_controller_running,
    get_controller_status,
    load_global_config,
    save_global_config,
    PrewarmConfig,
    should_apply_prewarm,
    pick_bucket,
)
from prewarm.plan_store import load_plan_from_redis, get_plan_metadata, get_plan_for_function
from prewarm.metrics_reader import record_function_call, get_all_ema_qps
from prewarm.eplb_adapter import get_time_info, compute_weight_distribution
from prewarm.k8s_applier import (
    K8S_AVAILABLE,
    init_k8s_client,
    create_k8s_applier_callback,
)


# ============================================================
# Lifespan Management
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager
    
    서버 시작 시:
    - Redis 연결 확인
    - Prewarm Controller 시작
    
    서버 종료 시:
    - Prewarm Controller 중지
    - Redis 연결 종료
    """
    print("[Server] Starting up...")
    
    # Redis 연결 확인
    try:
        redis = get_redis()
        redis.ping()
        print("[Server] Redis connection OK")
    except Exception as e:
        print(f"[Server] Redis connection failed: {e}")
    
    # K8s 클라이언트 초기화 (선택적)
    k8s_callback = None
    if K8S_AVAILABLE and os.getenv("ENABLE_K8S_APPLIER", "false").lower() == "true":
        if init_k8s_client():
            dry_run = os.getenv("K8S_DRY_RUN", "true").lower() == "true"
            k8s_callback = create_k8s_applier_callback(dry_run=dry_run)
            print(f"[Server] K8s applier enabled (dry_run={dry_run})")
    
    # Prewarm Controller 시작
    if os.getenv("ENABLE_PREWARM", "true").lower() == "true":
        start_prewarm_controller(on_plan_computed=k8s_callback)
    
    yield
    
    # 서버 종료 시 정리
    print("[Server] Shutting down...")
    stop_prewarm_controller()
    close_redis()


# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(
    title="AI Traffic Controller - Execution Server",
    description="Prewarm Controller + EPLB V2 실전용",
    version="2.0.0",
    root_path=os.getenv("API_ROOT_PATH", "/api"),
    lifespan=lifespan,
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Pydantic Models
# ============================================================

class ConfigUpdateRequest(BaseModel):
    mode: Optional[str] = None  # "off" | "on" | "experiment"
    alpha: Optional[float] = None
    interval: Optional[int] = None
    replicas: Optional[int] = None


class FunctionCallRequest(BaseModel):
    function_id: str
    workspace_id: Optional[str] = None


class FunctionCallResponse(BaseModel):
    function_id: str
    workspace_id: Optional[str]
    prewarm_enabled: bool
    experiment_bucket: Optional[str]
    desired_replicas: Optional[int]


# ============================================================
# Health Check Endpoints
# ============================================================

@app.get("/health")
async def health_check():
    """헬스 체크"""
    return {"status": "healthy"}


@app.get("/ready")
async def readiness_check():
    """준비 상태 체크"""
    try:
        redis = get_redis()
        redis.ping()
        return {
            "status": "ready",
            "redis": "connected",
            "prewarm_controller": is_controller_running(),
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Not ready: {e}")


# ============================================================
# Prewarm Controller Endpoints
# ============================================================

@app.get("/prewarm/status")
async def get_prewarm_status():
    """Prewarm Controller 상태 조회"""
    return get_controller_status()


@app.get("/prewarm/config")
async def get_prewarm_config():
    """현재 Prewarm 설정 조회"""
    config = load_global_config()
    return {
        "mode": config.mode,
        "alpha": config.alpha,
        "interval": config.interval,
        "replicas": config.replicas,
    }


@app.put("/prewarm/config")
async def update_prewarm_config(request: ConfigUpdateRequest):
    """Prewarm 설정 업데이트"""
    current = load_global_config()
    
    new_config = PrewarmConfig(
        mode=request.mode if request.mode else current.mode,
        alpha=request.alpha if request.alpha is not None else current.alpha,
        interval=request.interval if request.interval is not None else current.interval,
        replicas=request.replicas if request.replicas is not None else current.replicas,
    )
    
    # 유효성 검사
    if new_config.mode not in ["off", "on", "experiment"]:
        raise HTTPException(400, f"Invalid mode: {new_config.mode}")
    if not (0 < new_config.alpha <= 1):
        raise HTTPException(400, f"Alpha must be 0 < alpha <= 1")
    if new_config.interval < 1:
        raise HTTPException(400, "Interval must be >= 1")
    if new_config.replicas < 1:
        raise HTTPException(400, "Replicas must be >= 1")
    
    save_global_config(new_config)
    
    return {
        "message": "Config updated",
        "config": {
            "mode": new_config.mode,
            "alpha": new_config.alpha,
            "interval": new_config.interval,
            "replicas": new_config.replicas,
        }
    }


@app.post("/prewarm/start")
async def start_controller():
    """Prewarm Controller 시작"""
    if is_controller_running():
        return {"message": "Controller already running"}
    
    start_prewarm_controller()
    return {"message": "Controller started"}


@app.post("/prewarm/stop")
async def stop_controller():
    """Prewarm Controller 중지"""
    if not is_controller_running():
        return {"message": "Controller not running"}
    
    stop_prewarm_controller()
    return {"message": "Controller stopped"}


# ============================================================
# Plan Endpoints
# ============================================================

@app.get("/plan")
async def get_current_plan():
    """현재 plan 조회"""
    plan = load_plan_from_redis()
    metadata = get_plan_metadata()
    
    return {
        "plan": plan,
        "metadata": metadata,
        "total_replicas": sum(plan.values()) if plan else 0,
    }


@app.get("/plan/{function_id}")
async def get_function_plan(function_id: str):
    """특정 함수의 plan 조회"""
    replicas = get_plan_for_function(function_id)
    
    if replicas is None:
        raise HTTPException(404, f"No plan for function: {function_id}")
    
    return {
        "function_id": function_id,
        "desired_replicas": replicas,
    }


# ============================================================
# Metrics Endpoints
# ============================================================

@app.get("/metrics/ema")
async def get_ema_metrics():
    """모든 함수의 EMA QPS 조회"""
    ema_map = get_all_ema_qps()
    return {
        "ema_qps": ema_map,
        "count": len(ema_map),
    }


@app.get("/metrics/weights")
async def get_weight_distribution():
    """현재 가중치 분포 조회"""
    ema_map = get_all_ema_qps()
    weights = compute_weight_distribution(ema_map)
    time_info = get_time_info()
    
    return {
        "weights": weights,
        "time_info": time_info,
        "count": len(weights),
    }


@app.get("/metrics/time")
async def get_time_metrics():
    """시간대 정보 조회"""
    return get_time_info()


# ============================================================
# Function Call Endpoints (Routing 서버 연동)
# ============================================================

@app.post("/function/call", response_model=FunctionCallResponse)
async def handle_function_call(request: FunctionCallRequest):
    """
    함수 호출 처리 (Routing 서버에서 호출)
    
    - 호출 메트릭 기록
    - Prewarm 적용 여부 결정
    - 실험 버킷 결정
    """
    # 메트릭 기록
    record_function_call(request.function_id)
    
    # 현재 설정 로드
    config = load_global_config()
    
    # Prewarm 적용 여부 결정
    prewarm_enabled, bucket = should_apply_prewarm(
        request.workspace_id,
        config.mode,
    )
    
    # 현재 plan에서 replicas 조회
    desired_replicas = None
    if prewarm_enabled:
        desired_replicas = get_plan_for_function(request.function_id)
    
    return FunctionCallResponse(
        function_id=request.function_id,
        workspace_id=request.workspace_id,
        prewarm_enabled=prewarm_enabled,
        experiment_bucket=bucket,
        desired_replicas=desired_replicas,
    )


@app.get("/function/{function_id}/bucket")
async def get_function_bucket(
    function_id: str,
    workspace_id: Optional[str] = Query(None),
):
    """
    함수의 실험 버킷 조회 (A/B 테스트용)
    """
    config = load_global_config()
    prewarm_enabled, bucket = should_apply_prewarm(workspace_id, config.mode)
    
    return {
        "function_id": function_id,
        "workspace_id": workspace_id,
        "mode": config.mode,
        "prewarm_enabled": prewarm_enabled,
        "experiment_bucket": bucket,
    }


# ============================================================
# Debug Endpoints
# ============================================================

@app.get("/debug/redis/keys")
async def debug_redis_keys(pattern: str = "*"):
    """Redis 키 조회 (디버그용)"""
    redis = get_redis()
    
    cursor = 0
    keys = []
    while True:
        cursor, batch = redis.scan(cursor=cursor, match=pattern, count=100)
        keys.extend(batch)
        if cursor == 0 or len(keys) >= 1000:
            break
    
    return {
        "pattern": pattern,
        "keys": keys[:100],  # 최대 100개
        "total": len(keys),
    }


# ============================================================
# Run with uvicorn
# ============================================================

if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=os.getenv("DEBUG", "false").lower() == "true",
    )

