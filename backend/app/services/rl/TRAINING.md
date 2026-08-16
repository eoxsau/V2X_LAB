# V4 GNN-Reptile 학습 가이드

GATv2Conv Actor-Critic + Pointer Network 정책을 Reptile 메타강화학습으로 훈련하는 방법을 설명한다.

---

## 체크포인트 현황

| 파일 | 크기 | 상태 |
|------|------|------|
| `models/v4/v4_policy.pt` | 58 MB | **메인 체크포인트 — 이어서 학습할 때 이 파일 사용** |
| `models/maml_gnn_final.pt` | 5.4 MB | BC 웜스타트 초기 가중치 |

> 현재 **1,299 / 10,000 메타 이터레이션** 완료 (A100 80GB 기준 학습).  
> 목표: 10,000 이터레이션 완료 후 holdout 367개 구역 zero-shot 평가.

---

## 환경 설정

```bash
# Python 3.10+, CUDA 12+
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
pip install torch-geometric torch-scatter torch-sparse
pip install -r backend/requirements.txt
```

---

## 학습 실행

### 기본 (MAML/Reptile, 체크포인트 없이 처음부터)

```bash
cd /path/to/project
python -m backend.app.services.rl.v4.train_v4 \
    --trainer maml \
    --iters 10000
```

### ★ 체크포인트에서 이어서 학습 (권장)

```bash
python -m backend.app.services.rl.v4.train_v4 \
    --trainer maml \
    --resume backend/app/services/rl/models/v4/v4_policy.pt \
    --iters 10000
```

### BC 웜스타트 포함 처음부터 재학습

```bash
python -m backend.app.services.rl.v4.train_v4 \
    --trainer maml \
    --bc \
    --bc-tasks 300 \
    --bc-epochs 30 \
    --iters 10000
```

### GPU 속도 최적화 옵션 (A100 권장)

```bash
python -m backend.app.services.rl.v4.train_v4 \
    --trainer maml \
    --resume backend/app/services/rl/models/v4/v4_policy.pt \
    --iters 10000 \
    --compile-model \
    --collect-once
```

- `--compile-model`: `torch.compile` 활성화, 처리량 +20~30%
- `--collect-once`: 에피소드 수집 1회 후 inner loop 전체 재사용, +30~40%

### CPU 디버그 (소규모 테스트)

```bash
python -m backend.app.services.rl.v4.train_v4 \
    --trainer maml \
    --device cpu \
    --iters 20 \
    --bc-tasks 5
```

---

## 주요 하이퍼파라미터

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `meta_iterations` | 10,000 | 전체 메타 이터레이션 목표 |
| `n_tasks_per_iter` | 16 | 이터레이션당 샘플링 태스크 수 |
| `inner_steps` | 5 | 태스크당 inner SGD 스텝 수 (Reptile K) |
| `inner_lr` | 3e-3 | Inner 학습률 |
| `meta_lr` | 5e-4 | Meta(outer) 학습률, cosine decay |
| `support_episodes` | 8 | 태스크당 에피소드 수 |
| `max_episode_steps` | 100 | 에피소드당 최대 스텝 |
| `curriculum_warmup_iters` | 1,000 | 커리큘럼 단계 증가 구간 |
| `save_every` | 50 | 체크포인트 저장 주기 |
| `use_icm` | True | Intrinsic Curiosity Module |
| `use_her` | True | Hindsight Experience Replay |
| `her_ratio` | 0.6 | HER 재활용 비율 |
| `ppo_epochs` | 4 | PPO inner 그래디언트 반복 수 |
| `use_amp` | True | Mixed precision (A100) |

---

## 데이터 분할

- 전국 행정구역 **2,367개** (regions.db)
- **학습용: 2,000개** / **holdout: 367개** (seed=42 고정)
- 매 에피소드마다 도메인 랜덤화:
  - 구역, 기지국 배치 (1~30개), RSU (0~15개)
  - 출발/목적지, 교통 밀도 (0.5~300 veh/km²)
  - 이종망 혼재 (4G/5G/6G, 45% 확률)

---

## 모델 아키텍처

- **GATv2Conv** 그래프 어텐션 3레이어 (edge-featured)
- **Pointer Network** 디코더 — 노드 선택 확률 출력
- **Actor-Critic**: 공유 GNN 인코더, 분리된 Policy / Value head
- 노드 특징: 15차원 (거리·지연·부하·커버리지 등)
- 엣지 특징: 8차원 (도로 길이·차폐·핸드오버 비용 등)
- 절대 좌표 미사용 → 처음 보는 구역 zero-shot 동작

---

## 학습된 정책 로드 (추론)

```python
from backend.app.services.rl.v4.universal_gnn_policy import UniversalGNNPolicy

policy = UniversalGNNPolicy.load("backend/app/services/rl/models/v4/v4_policy.pt")
policy.eval()
```

---

## 체크포인트 저장 경로

체크포인트는 `backend/app/services/rl/models/` 아래에 저장된다:

```
models/
├── maml_gnn_final.pt        # BC 웜스타트 초기 가중치 (Git LFS)
└── v4/
    └── v4_policy.pt         # 현재 메인 체크포인트 (Git LFS)
```

중간 체크포인트는 `maml_gnn_{iter}.pt` 형태로 저장되며, 학습 재개 시 가장 최신 파일을 `--resume`에 넘기면 된다.
