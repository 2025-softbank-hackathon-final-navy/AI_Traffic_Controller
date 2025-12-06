# AI Traffic Controller

**[한국어](../README.md)** | **English** | **[日本語](./README_JP.md)**

AI-based Prewarm Controller utilizing DeepSeek's EPLB (Expert Parallelism Load Balancer)

## Overview

This project is a time-series ML-based Prewarm system designed to solve the Cold Start problem in serverless functions.
It combines QPS (Queries Per Second) based EMA (Exponential Moving Average) prediction with the EPLB algorithm to calculate optimal replica distribution plans.

## Key Features

- **EPLB-based Load Balancing**: Applies DeepSeek's Expert Parallelism Load Balancer algorithm
- **EMA-based Traffic Prediction**: Predicts future load using exponential moving average of time-series QPS data
- **Time-based Weight Adjustment**: Reflects traffic patterns according to time of day
- **A/B Experiment Support**: Separates control/treatment buckets per workspace
- **K8s Integration**: Automatic Deployment replica adjustment (optional)

## Project Structure

```
AI_Traffic_Controller/
├── main.py                     # FastAPI server entry point
├── requirements.txt            # Python dependencies
├── core/
│   ├── __init__.py
│   └── redis.py               # Redis client and key management
├── eplb_core/                  # EPLB core module
│   ├── __init__.py
│   ├── eplb.py                # DeepSeek original algorithm
│   └── engine.py              # Wrapper API
└── prewarm/                    # Prewarm Controller module
    ├── __init__.py
    ├── metrics_reader.py      # QPS/EMA collection and update
    ├── eplb_adapter.py        # Prewarm-specific adapter
    ├── plan_store.py          # Redis plan storage/retrieval
    ├── controller.py          # Main scheduler + A/B logic
    └── k8s_applier.py         # K8s Deployment integration
```

## Installation & Running

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run Redis

```bash
# Run Redis with Docker
docker run -d --name redis -p 6379:6379 redis:latest
```

### 3. Set Environment Variables

```bash
# Redis settings
export REDIS_HOST=localhost
export REDIS_PORT=6379

# Server settings
export HOST=0.0.0.0
export PORT=8000

# Prewarm settings
export ENABLE_PREWARM=true

# K8s settings (optional)
export ENABLE_K8S_APPLIER=false
export K8S_DRY_RUN=true
```

### 4. Run Server

```bash
python main.py

# Or run uvicorn directly
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## API Endpoints

### Health Check

- `GET /health` - Health check
- `GET /ready` - Readiness check

### Prewarm Controller

- `GET /prewarm/status` - Get Controller status
- `GET /prewarm/config` - Get configuration
- `PUT /prewarm/config` - Update configuration
- `POST /prewarm/start` - Start Controller
- `POST /prewarm/stop` - Stop Controller

### Plan Management

- `GET /plan` - Get all current plans
- `GET /plan/{function_id}` - Get specific function plan

### Metrics

- `GET /metrics/ema` - Get EMA QPS
- `GET /metrics/weights` - Get weight distribution
- `GET /metrics/time` - Get time information

### Function Calls (Routing Server Integration)

- `POST /function/call` - Process function call and determine experiment bucket
- `GET /function/{function_id}/bucket` - Get experiment bucket

## Redis Key Schema

```text
# Metrics (recorded by Routing server)
metrics:function:{function_id}:calls:{epoch_minute}

# EMA state
state:function:{function_id}:ema_qps

# Prewarm plan
plan:function:{function_id}:desired_replicas

# Global settings
config:prewarm:mode              # "off" | "on" | "experiment"
config:prewarm:global_replicas   # int
config:prewarm:ema_alpha         # float
config:prewarm:interval_sec      # int

# Workspace settings (optional)
config:workspace:{workspace_id}:prewarm_mode
config:workspace:{workspace_id}:aggressiveness
```

## Prewarm Modes

| Mode | Description |
|------|-------------|
| `off` | Prewarm completely disabled |
| `on` | Apply Prewarm to all functions |
| `experiment` | A/B experiment mode (bucket separation per workspace) |

## A/B Experiments

In `experiment` mode, workspace_id is hashed to separate into `control` and `treatment` buckets:

- **treatment**: EPLB plan applied
- **control**: EPLB plan not applied (existing method)

The bucket ratio defaults to 50:50, and deterministic hashing (CRC32) ensures the same workspace is always assigned to the same bucket.

## Time Coefficients

Reflects different traffic patterns by time of day:

| Time | Coefficient | Description |
|------|-------------|-------------|
| 0-5h | 0.6 | Late night (low traffic) |
| 6-8h | 0.9 | Early morning (starting to increase) |
| 9-18h | 1.3 | Business hours (high traffic) |
| 19-22h | 1.5 | Evening peak (highest traffic) |
| 23h | 0.8 | Late night (starting to decrease) |

## EPLB Algorithm

DeepSeek's Expert Parallelism Load Balancer was developed for load balancing in MoE (Mixture of Experts) models.
This project applies it to serverless function replica distribution:

- **Input**: Weight of each function (EMA QPS × Time Coefficient × Aggressiveness)
- **Output**: Number of replicas to assign to each function
- **Goal**: Distribute total load as evenly as possible

### MoE ↔ Serverless Prewarm Concept Mapping

| MoE (Original) | Serverless Prewarm (Ours) | Description |
|----------------|---------------------------|-------------|
| **Expert** | **Function** | In MoE, each Expert is a specialist network handling specific input patterns. In our system, each serverless function corresponds to an Expert |
| **Token** | **Request** | Tokens routed in MoE. In our system, API requests coming to functions |
| **Expert Load (weight)** | **EMA QPS** | Number of tokens processed by Expert. In our system, time-series average request volume (with EMA applied) |
| **Physical Expert** | **Replica** | Expert instance deployed on actual GPU. In our system, function execution instance (Pod/Container) |
| **Logical Expert** | **Function ID** | Logical Expert identifier. In our system, unique identifier of the function |
| **Expert Replication** | **Replica Scaling** | Replicate high-load Experts to distribute. In our system, increase replicas for high-QPS functions |
| **GPU** | **Worker Node** | Physical resource where Expert is deployed. In our system, K8s worker node |
| **Expert Group** | **Function Group** | Expert groups routed together. In our system, functions in the same workspace |

### Algorithm Flow Mapping

```
 Token Statistics  →  Expert Weight  →  Rebalance  →  Expert Assignment   
   (Token stats)       (Expert weight)   (Rebalance)     (Expert placement)         
                                 
QPS Metrics  →  EMA + Time Coeff  →  EPLB Engine  →  Replica Plan         
 (Request stats)  (Time-series weight)  (Rebalance calc)  (Replica placement plan)    
```

### Why EPLB?

1. **Load Equalization**: EPLB is designed to minimize load imbalance between Experts => Equalize Cold Start probability across functions

2. **Dynamic Replication**: Automatically replicate high-load Experts => Allocate more replicas to high-QPS functions

3. **Hierarchical Placement**: Considers Node/GPU hierarchy => Naturally maps to K8s Node/Pod structure

4. **Proven Algorithm**: Validated in DeepSeek production => Guaranteed stability and performance

## License

- EPLB Algorithm: MIT License (DeepSeek)
- This Project: MIT License

