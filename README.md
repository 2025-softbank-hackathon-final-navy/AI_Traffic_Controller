# AI Traffic Controller

**한국어** | **[English](./docs/README_EN.md)** | **[日本語](./docs/README_JP.md)**

DeepSeek사의 EPLB(Expert Parallelism Load Balancer)를 활용한 AI 기반 Prewarm Controller

## 개요

이 프로젝트는 서버리스 함수의 Cold Start 문제를 해결하기 위한 시계열 ML기반 Prewarm 시스템입니다.
QPS(Queries Per Second) 기반 EMA(Exponential Moving Average) 예측과 EPLB 알고리즘을 결합하여
최적의 replica 분배 계획을 계산합니다.

## 주요 기능

- **EPLB 기반 로드 밸런싱**: DeepSeek의 Expert Parallelism Load Balancer 알고리즘 적용
- **EMA 기반 트래픽 예측**: 시계열 QPS 데이터의 지수 이동 평균으로 미래 부하 예측
- **시간대별 가중치 조정**: 시간대에 따른 트래픽 패턴 반영
- **A/B 실험 지원**: workspace 단위 control/treatment 버킷 분리
- **K8s 연동**: Deployment replica 자동 조정 (선택적)

## 프로젝트 구조

```
AI_Traffic_Controller/
├── main.py                     # FastAPI 서버 진입점
├── requirements.txt            # Python 의존성
├── core/
│   ├── __init__.py
│   └── redis.py               # Redis 클라이언트 및 키 관리
├── eplb_core/                  # EPLB 핵심 모듈
│   ├── __init__.py
│   ├── eplb.py                # DeepSeek 원본 알고리즘
│   └── engine.py              # 래퍼 API
└── prewarm/                    # Prewarm Controller 모듈
    ├── __init__.py
    ├── metrics_reader.py      # QPS/EMA 수집 및 갱신
    ├── eplb_adapter.py        # Prewarm 전용 어댑터
    ├── plan_store.py          # Redis plan 저장/조회
    ├── controller.py          # 메인 스케줄러 + A/B 로직
    └── k8s_applier.py         # K8s Deployment 연동
```

## 설치 및 실행

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. Redis 실행

```bash
# Docker로 Redis 실행
docker run -d --name redis -p 6379:6379 redis:latest
```

### 3. 환경변수 설정

```bash
# Redis 설정
export REDIS_HOST=localhost
export REDIS_PORT=6379

# 서버 설정
export HOST=0.0.0.0
export PORT=8000

# Prewarm 설정
export ENABLE_PREWARM=true

# K8s 설정 (선택적)
export ENABLE_K8S_APPLIER=false
export K8S_DRY_RUN=true
```

### 4. 서버 실행

```bash
python main.py

# 또는 uvicorn 직접 실행
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## API 엔드포인트

### Health Check

- `GET /health` - 헬스 체크
- `GET /ready` - 준비 상태 체크

### Prewarm Controller

- `GET /prewarm/status` - Controller 상태 조회
- `GET /prewarm/config` - 설정 조회
- `PUT /prewarm/config` - 설정 업데이트
- `POST /prewarm/start` - Controller 시작
- `POST /prewarm/stop` - Controller 중지

### Plan 관리

- `GET /plan` - 현재 plan 전체 조회
- `GET /plan/{function_id}` - 특정 함수 plan 조회

### Metrics

- `GET /metrics/ema` - EMA QPS 조회
- `GET /metrics/weights` - 가중치 분포 조회
- `GET /metrics/time` - 시간대 정보 조회

### 함수 호출 (Routing 서버 연동)

- `POST /function/call` - 함수 호출 처리 및 실험 버킷 결정
- `GET /function/{function_id}/bucket` - 실험 버킷 조회

## Redis 키 스키마

```text
# 메트릭 (Routing 서버에서 기록)
metrics:function:{function_id}:calls:{epoch_minute}

# EMA 상태
state:function:{function_id}:ema_qps

# Prewarm 계획
plan:function:{function_id}:desired_replicas

# 전역 설정
config:prewarm:mode              # "off" | "on" | "experiment"
config:prewarm:global_replicas   # int
config:prewarm:ema_alpha         # float
config:prewarm:interval_sec      # int

# Workspace 설정 (옵션)
config:workspace:{workspace_id}:prewarm_mode
config:workspace:{workspace_id}:aggressiveness
```

## Prewarm 모드

| 모드 | 설명 |
|------|------|
| `off` | Prewarm 완전 비활성 |
| `on` | 모든 함수에 Prewarm 적용 |
| `experiment` | A/B 실험 모드 (workspace별 버킷 분리) |

## A/B 실험

`experiment` 모드에서는 workspace_id를 해시하여 `control`과 `treatment` 버킷으로 분리합니다:

- **treatment**: EPLB 계획 적용
- **control**: EPLB 계획 미적용 (기존 방식)

버킷 비율은 기본 50:50이며, 결정적 해시(CRC32)를 사용하여 동일 workspace는 항상 같은 버킷에 할당됩니다.

## 시간대 계수

시간대별로 다른 트래픽 패턴을 반영합니다:

| 시간대 | 계수 | 설명 |
|--------|------|------|
| 0~5시 | 0.6 | 심야 (낮은 트래픽) |
| 6~8시 | 0.9 | 이른 아침 (증가 시작) |
| 9~18시 | 1.3 | 업무 시간 (높은 트래픽) |
| 19~22시 | 1.5 | 저녁 피크 (최고 트래픽) |
| 23시 | 0.8 | 늦은 밤 (감소 시작) |

## EPLB 알고리즘

DeepSeek의 Expert Parallelism Load Balancer는 MoE(Mixture of Experts) 모델의 로드 밸런싱을 위해 개발되었습니다.
이 프로젝트에서는 이를 서버리스 함수의 replica 분배에 적용합니다:

- **입력**: 각 함수의 가중치 (EMA QPS × 시간대 계수 × 공격성)
- **출력**: 각 함수에 할당할 replica 수
- **목표**: 전체 부하를 최대한 균등하게 분배

### MoE ↔ Serverless Prewarm 개념 매핑

| MoE (원본) | Serverless Prewarm (우리) | 설명 |
|------------|---------------------------|------|
| **Expert** | **Function** | MoE에서 각 Expert는 특정 입력 패턴을 처리하는 전문가 네트워크. 우리 시스템에서는 각 서버리스 함수가 Expert에 해당 |
| **Token** | **Request** | MoE에서 라우팅되는 토큰. 우리 시스템에서는 함수로 들어오는 API 요청 |
| **Expert Load (weight)** | **EMA QPS** | Expert가 처리하는 토큰 수. 우리 시스템에서는 함수의 시계열 평균 요청량 (EMA 적용) |
| **Physical Expert** | **Replica** | 실제 GPU에 배치된 Expert 인스턴스. 우리 시스템에서는 함수의 실행 인스턴스 (Pod/Container) |
| **Logical Expert** | **Function ID** | 논리적 Expert 식별자. 우리 시스템에서는 함수의 고유 식별자 |
| **Expert Replication** | **Replica Scaling** | 부하가 높은 Expert를 복제하여 분산. 우리 시스템에서는 QPS가 높은 함수의 replica 증가 |
| **GPU** | **Worker Node** | Expert가 배치되는 물리 자원. 우리 시스템에서는 K8s 워커 노드 |
| **Expert Group** | **Function Group** | 함께 라우팅되는 Expert 그룹. 우리 시스템에서는 동일 워크스페이스의 함수들 |

### 알고리즘 흐름 매핑

```
 Token Statistics  →  Expert Weight  →  Rebalance  →  Expert Assignment   
   (토큰 통계)           (Expert 가중치)    (재분배)          (Expert 배치)         
                                 
QPS Metrics  →  EMA + Time Coeff  →  EPLB Engine  →  Replica Plan         
  (요청 통계)      (시계열 가중치)         (재분배 계산)      (replica 배치 계획)    

```

### 왜 EPLB인가?

1. **부하 균등화**: EPLB는 Expert 간 부하 불균형을 최소화하도록 설계됨 => 함수 간 Cold Start 확률 균등화

2. **동적 복제**: 부하가 높은 Expert를 자동 복제 => QPS 높은 함수에 더 많은 replica 할당

3. **계층적 배치**: Node/GPU 계층 구조 고려 => K8s Node/Pod 구조에 자연스럽게 매핑

4. **검증된 알고리즘**: DeepSeek 프로덕션에서 검증 => 안정성과 성능 보장

## 라이선스

- EPLB 알고리즘: MIT License (DeepSeek)
- 본 프로젝트: MIT License
