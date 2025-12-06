"""
K8s Applier Module

function_id -> desired_replicas를 실제 K8s Deployment에 반영
"""

import os
from typing import Dict, Optional, Callable
from dataclasses import dataclass

# K8s 클라이언트는 선택적 의존성
try:
    from kubernetes import client, config as k8s_config
    from kubernetes.client.rest import ApiException
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


@dataclass
class K8sConfig:
    """K8s 설정"""
    namespace: str = "default"
    in_cluster: bool = False  # True면 클러스터 내부에서 실행
    kubeconfig_path: Optional[str] = None


# 전역 K8s 클라이언트
_k8s_apps_api: Optional[object] = None
_k8s_config: Optional[K8sConfig] = None


def init_k8s_client(config: Optional[K8sConfig] = None) -> bool:
    """
    K8s 클라이언트 초기화
    
    Parameters:
        config: K8s 설정 (None이면 환경변수/기본값 사용)
        
    Returns:
        초기화 성공 여부
    """
    global _k8s_apps_api, _k8s_config
    
    if not K8S_AVAILABLE:
        print("[K8s] kubernetes 패키지가 설치되지 않음")
        return False
    
    if config is None:
        config = K8sConfig(
            namespace=os.getenv("K8S_NAMESPACE", "default"),
            in_cluster=os.getenv("K8S_IN_CLUSTER", "false").lower() == "true",
            kubeconfig_path=os.getenv("KUBECONFIG", None),
        )
    
    try:
        if config.in_cluster:
            k8s_config.load_incluster_config()
        elif config.kubeconfig_path:
            k8s_config.load_kube_config(config_file=config.kubeconfig_path)
        else:
            k8s_config.load_kube_config()
        
        _k8s_apps_api = client.AppsV1Api()
        _k8s_config = config
        
        print(f"[K8s] 클라이언트 초기화 완료 (namespace={config.namespace})")
        return True
        
    except Exception as e:
        print(f"[K8s] 클라이언트 초기화 실패: {e}")
        return False


def get_deployment_name(function_id: str) -> str:
    """
    function_id로부터 K8s Deployment 이름 생성
    
    실제 환경에서는 매핑 테이블이나 함수 메타데이터에서 가져올 수 있음
    """
    # 간단한 변환 규칙 (실제로는 DB/설정에서 매핑)
    # function_id의 특수문자를 '-'로 변환
    safe_name = function_id.lower().replace("_", "-").replace(".", "-")
    return f"func-{safe_name}"


def scale_deployment(
    deployment_name: str,
    replicas: int,
    namespace: Optional[str] = None,
) -> bool:
    """
    Deployment의 replica 수 조정
    
    Parameters:
        deployment_name: Deployment 이름
        replicas: 목표 replica 수
        namespace: 네임스페이스 (None이면 기본값 사용)
        
    Returns:
        성공 여부
    """
    global _k8s_apps_api, _k8s_config
    
    if _k8s_apps_api is None:
        print("[K8s] 클라이언트가 초기화되지 않음")
        return False
    
    if namespace is None:
        namespace = _k8s_config.namespace if _k8s_config else "default"
    
    try:
        # Scale 패치
        body = {"spec": {"replicas": replicas}}
        _k8s_apps_api.patch_namespaced_deployment_scale(
            name=deployment_name,
            namespace=namespace,
            body=body,
        )
        print(f"[K8s] {namespace}/{deployment_name} scaled to {replicas}")
        return True
        
    except ApiException as e:
        if e.status == 404:
            print(f"[K8s] Deployment not found: {namespace}/{deployment_name}")
        else:
            print(f"[K8s] Scale failed: {e}")
        return False
    except Exception as e:
        print(f"[K8s] Unexpected error: {e}")
        return False


def apply_plan_with_k8s(
    plan: Dict[str, int],
    function_to_deployment: Optional[Dict[str, str]] = None,
    namespace: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, bool]:
    """
    function_id -> desired_replicas를 실제 K8s Deployment에 반영
    
    Parameters:
        plan: function_id -> desired_replicas 맵
        function_to_deployment: function_id -> deployment_name 매핑 (없으면 자동 생성)
        namespace: 네임스페이스
        dry_run: True면 실제 적용하지 않고 로그만 출력
        
    Returns:
        function_id -> 성공여부 맵
    """
    results: Dict[str, bool] = {}
    
    if not K8S_AVAILABLE:
        print("[K8s] kubernetes 패키지가 설치되지 않음")
        return {func_id: False for func_id in plan}
    
    for function_id, replicas in plan.items():
        # Deployment 이름 결정
        if function_to_deployment and function_id in function_to_deployment:
            deployment_name = function_to_deployment[function_id]
        else:
            deployment_name = get_deployment_name(function_id)
        
        if dry_run:
            print(f"[K8s DryRun] Would scale {deployment_name} to {replicas}")
            results[function_id] = True
        else:
            success = scale_deployment(deployment_name, replicas, namespace)
            results[function_id] = success
    
    return results


def get_current_replicas(
    deployment_name: str,
    namespace: Optional[str] = None,
) -> Optional[int]:
    """
    현재 Deployment의 replica 수 조회
    
    Returns:
        현재 replica 수 또는 None (조회 실패)
    """
    global _k8s_apps_api, _k8s_config
    
    if _k8s_apps_api is None:
        return None
    
    if namespace is None:
        namespace = _k8s_config.namespace if _k8s_config else "default"
    
    try:
        deployment = _k8s_apps_api.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
        )
        return deployment.spec.replicas
    except:
        return None


def create_k8s_applier_callback(
    function_to_deployment: Optional[Dict[str, str]] = None,
    namespace: Optional[str] = None,
    dry_run: bool = False,
) -> Callable[[Dict[str, int]], None]:
    """
    Prewarm Controller에 전달할 콜백 함수 생성
    
    Usage:
        callback = create_k8s_applier_callback(dry_run=True)
        start_prewarm_controller(on_plan_computed=callback)
    """
    def callback(plan: Dict[str, int]) -> None:
        apply_plan_with_k8s(
            plan,
            function_to_deployment=function_to_deployment,
            namespace=namespace,
            dry_run=dry_run,
        )
    
    return callback


__all__ = [
    'K8sConfig',
    'K8S_AVAILABLE',
    'init_k8s_client',
    'get_deployment_name',
    'scale_deployment',
    'apply_plan_with_k8s',
    'get_current_replicas',
    'create_k8s_applier_callback',
]

