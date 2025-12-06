# AI Traffic Controller

**[한국어](../README.md)** | **[English](./README_EN.md)** | **日本語**

DeepSeek社のEPLB（Expert Parallelism Load Balancer）を活用したAIベースPrewarm Controller

## 概要

本プロジェクトは、サーバーレス関数のCold Start問題を解決するための時系列MLベースPrewarmシステムです。
QPS（Queries Per Second）ベースのEMA（Exponential Moving Average）予測とEPLBアルゴリズムを組み合わせて、最適なレプリカ分配計画を算出します。

## 主な機能

- **EPLBベースのロードバランシング**: DeepSeekのExpert Parallelism Load Balancerアルゴリズムを適用
- **EMAベースのトラフィック予測**: 時系列QPSデータの指数移動平均で将来の負荷を予測
- **時間帯別の重み調整**: 時間帯に応じたトラフィックパターンを反映
- **A/B実験サポート**: ワークスペース単位でcontrol/treatmentバケットを分離
- **K8s連携**: Deploymentレプリカの自動調整（オプション）

## プロジェクト構造

```
AI_Traffic_Controller/
├── main.py                     # FastAPIサーバーエントリーポイント
├── requirements.txt            # Python依存関係
├── core/
│   ├── __init__.py
│   └── redis.py               # Redisクライアントとキー管理
├── eplb_core/                  # EPLBコアモジュール
│   ├── __init__.py
│   ├── eplb.py                # DeepSeekオリジナルアルゴリズム
│   └── engine.py              # ラッパーAPI
└── prewarm/                    # Prewarm Controllerモジュール
    ├── __init__.py
    ├── metrics_reader.py      # QPS/EMAの収集と更新
    ├── eplb_adapter.py        # Prewarm専用アダプター
    ├── plan_store.py          # Redisプラン保存/取得
    ├── controller.py          # メインスケジューラー + A/Bロジック
    └── k8s_applier.py         # K8s Deployment連携
```

## インストールと実行

### 1. 依存関係のインストール

```bash
pip install -r requirements.txt
```

### 2. Redisの実行

```bash
# DockerでRedisを実行
docker run -d --name redis -p 6379:6379 redis:latest
```

### 3. 環境変数の設定

```bash
# Redis設定
export REDIS_HOST=localhost
export REDIS_PORT=6379

# サーバー設定
export HOST=0.0.0.0
export PORT=8000

# Prewarm設定
export ENABLE_PREWARM=true

# K8s設定（オプション）
export ENABLE_K8S_APPLIER=false
export K8S_DRY_RUN=true
```

### 4. サーバーの実行

```bash
python main.py

# またはuvicornを直接実行
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## APIエンドポイント

### ヘルスチェック

- `GET /health` - ヘルスチェック
- `GET /ready` - レディネスチェック

### Prewarm Controller

- `GET /prewarm/status` - Controllerステータス取得
- `GET /prewarm/config` - 設定取得
- `PUT /prewarm/config` - 設定更新
- `POST /prewarm/start` - Controller開始
- `POST /prewarm/stop` - Controller停止

### プラン管理

- `GET /plan` - 現在のプラン全体取得
- `GET /plan/{function_id}` - 特定関数のプラン取得

### メトリクス

- `GET /metrics/ema` - EMA QPS取得
- `GET /metrics/weights` - 重み分布取得
- `GET /metrics/time` - 時間情報取得

### 関数呼び出し（Routingサーバー連携）

- `POST /function/call` - 関数呼び出し処理と実験バケット決定
- `GET /function/{function_id}/bucket` - 実験バケット取得

## Redisキースキーマ

```text
# メトリクス（Routingサーバーで記録）
metrics:function:{function_id}:calls:{epoch_minute}

# EMA状態
state:function:{function_id}:ema_qps

# Prewarm計画
plan:function:{function_id}:desired_replicas

# グローバル設定
config:prewarm:mode              # "off" | "on" | "experiment"
config:prewarm:global_replicas   # int
config:prewarm:ema_alpha         # float
config:prewarm:interval_sec      # int

# ワークスペース設定（オプション）
config:workspace:{workspace_id}:prewarm_mode
config:workspace:{workspace_id}:aggressiveness
```

## Prewarmモード

| モード | 説明 |
|--------|------|
| `off` | Prewarm完全無効 |
| `on` | すべての関数にPrewarmを適用 |
| `experiment` | A/B実験モード（ワークスペース別バケット分離） |

## A/B実験

`experiment`モードでは、workspace_idをハッシュして`control`と`treatment`バケットに分離します：

- **treatment**: EPLBプランを適用
- **control**: EPLBプランを未適用（既存方式）

バケット比率はデフォルトで50:50であり、決定論的ハッシュ（CRC32）を使用して同じワークスペースは常に同じバケットに割り当てられます。

## 時間帯係数

時間帯別に異なるトラフィックパターンを反映します：

| 時間帯 | 係数 | 説明 |
|--------|------|------|
| 0〜5時 | 0.6 | 深夜（低トラフィック） |
| 6〜8時 | 0.9 | 早朝（増加開始） |
| 9〜18時 | 1.3 | 業務時間（高トラフィック） |
| 19〜22時 | 1.5 | 夕方ピーク（最高トラフィック） |
| 23時 | 0.8 | 深夜（減少開始） |

## EPLBアルゴリズム

DeepSeekのExpert Parallelism Load Balancerは、MoE（Mixture of Experts）モデルのロードバランシングのために開発されました。
本プロジェクトではこれをサーバーレス関数のレプリカ分配に適用します：

- **入力**: 各関数の重み（EMA QPS × 時間帯係数 × アグレッシブネス）
- **出力**: 各関数に割り当てるレプリカ数
- **目標**: 全体の負荷を可能な限り均等に分配

### MoE ↔ Serverless Prewarm コンセプトマッピング

| MoE（オリジナル） | Serverless Prewarm（私たち） | 説明 |
|-------------------|------------------------------|------|
| **Expert** | **Function** | MoEでは各Expertが特定の入力パターンを処理する専門家ネットワーク。私たちのシステムでは各サーバーレス関数がExpertに対応 |
| **Token** | **Request** | MoEでルーティングされるトークン。私たちのシステムでは関数に入ってくるAPIリクエスト |
| **Expert Load (weight)** | **EMA QPS** | Expertが処理するトークン数。私たちのシステムでは関数の時系列平均リクエスト量（EMA適用） |
| **Physical Expert** | **Replica** | 実際のGPUに配置されたExpertインスタンス。私たちのシステムでは関数の実行インスタンス（Pod/Container） |
| **Logical Expert** | **Function ID** | 論理的なExpert識別子。私たちのシステムでは関数の一意識別子 |
| **Expert Replication** | **Replica Scaling** | 高負荷のExpertを複製して分散。私たちのシステムではQPSが高い関数のレプリカを増加 |
| **GPU** | **Worker Node** | Expertが配置される物理リソース。私たちのシステムではK8sワーカーノード |
| **Expert Group** | **Function Group** | 一緒にルーティングされるExpertグループ。私たちのシステムでは同じワークスペースの関数群 |

### アルゴリズムフローマッピング

```
 Token Statistics  →  Expert Weight  →  Rebalance  →  Expert Assignment   
  (トークン統計)       (Expert重み)       (再分配)        (Expert配置)         
                                 
QPS Metrics  →  EMA + Time Coeff  →  EPLB Engine  →  Replica Plan         
 (リクエスト統計)   (時系列重み)         (再分配計算)      (レプリカ配置計画)    
```

### なぜEPLBなのか？

1. **負荷均等化**: EPLBはExpert間の負荷不均衡を最小化するように設計 => 関数間のCold Start確率を均等化

2. **動的レプリケーション**: 高負荷のExpertを自動複製 => QPSの高い関数により多くのレプリカを割り当て

3. **階層的配置**: Node/GPU階層構造を考慮 => K8s Node/Pod構造に自然にマッピング

4. **実証済みアルゴリズム**: DeepSeekのプロダクションで検証済み => 安定性とパフォーマンスを保証

## ライセンス

- EPLBアルゴリズム: MIT License (DeepSeek)
- 本プロジェクト: MIT License

