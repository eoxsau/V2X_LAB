#!/usr/bin/env python3
"""
V4 Universal GNN Policy — FOMAML 학습 스크립트

사용법 (Mac / CPU):
    python train_v4.py

사용법 (A100):
    python train_v4.py --device cuda --tasks 16 --iters 10000 --max-nodes 2000

재시작 (checkpoint 이어서):
    python train_v4.py --resume app/services/rl/models/maml_gnn_500.pt

References:
    Finn et al., MAML, ICML 2017
    Nichol et al., FOMAML, arXiv 2018
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

# backend/ 기준 실행 가정
sys.path.insert(0, str(Path(__file__).parent))

from app.services.rl.v4.domain_randomizer import DomainRandomizer
from app.services.rl.v4.maml_trainer import MAMLConfig, MAMLTrainer


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V4 GNN FOMAML 학습",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # 학습 규모
    p.add_argument("--iters",      type=int,   default=10_000, help="총 meta-iteration 수")
    p.add_argument("--tasks",      type=int,   default=4,      help="iteration당 task 수 (A100: 16)")
    p.add_argument("--inner-steps",type=int,   default=5,      help="task당 inner gradient step")
    p.add_argument("--support",    type=int,   default=2,      help="support set episode 수")
    p.add_argument("--query",      type=int,   default=2,      help="query set episode 수")

    # 그래프 크기 (Mac에서는 300, A100은 2000)
    p.add_argument("--max-nodes",  type=int,   default=300,    help="학습 그래프 최대 노드 수")

    # 장치
    p.add_argument("--device",     type=str,   default="auto",
                   help="'auto' | 'cpu' | 'mps' | 'cuda'")
    p.add_argument("--no-compile", action="store_true",
                   help="torch.compile 비활성화 (디버깅용)")

    # 저장 / 재시작
    p.add_argument("--save-dir",   type=str,   default="app/services/rl/models",
                   help="체크포인트 저장 경로")
    p.add_argument("--save-every", type=int,   default=100,    help="N iter마다 체크포인트")
    p.add_argument("--log-every",  type=int,   default=10,     help="N iter마다 로그 출력")
    p.add_argument("--resume",     type=str,   default=None,   help="체크포인트 경로 (재시작)")

    # 학습률
    p.add_argument("--meta-lr",    type=float, default=3e-4,   help="메타 학습률 β")
    p.add_argument("--inner-lr",   type=float, default=1e-3,   help="inner loop 학습률 α")
    p.add_argument("--lr-schedule",type=str,   default="cosine",
                   choices=["constant","cosine","linear"],      help="메타 LR 스케줄")

    # 기타
    p.add_argument("--seed",       type=int,   default=42)
    p.add_argument("--pre-cache",  action="store_true",
                   help="학습 전 전체 구역 그래프 캐시 (A100 권장)")
    return p.parse_args()


def resolve_device(requested: str) -> str:
    import torch
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def main() -> None:
    args = parse_args()

    device = resolve_device(args.device)
    print(f"[train_v4] device={device}  max_nodes={args.max_nodes}")
    print(f"           {args.tasks} tasks/iter × {args.iters} iters")
    print(f"           inner_steps={args.inner_steps}  support={args.support}  query={args.query}")
    print(f"           save_dir={args.save_dir}  save_every={args.save_every}")
    if args.resume:
        print(f"           resume={args.resume}")
    print()

    # ── 1. DomainRandomizer ────────────────────────────────────────────────────
    dr = DomainRandomizer(train=True, seed=args.seed, max_nodes=args.max_nodes)
    print(f"[train_v4] regions: train={len(dr._train_regions)}  holdout={len(dr._holdout_regions)}")

    if args.pre_cache:
        print("[train_v4] 전체 구역 캐시 중 (처음에만 필요)…")
        dr.pre_cache_all(verbose=True)

    # ── 2. MAMLConfig ─────────────────────────────────────────────────────────
    cfg = MAMLConfig(
        n_tasks_per_iter    = args.tasks,
        meta_iterations     = args.iters,
        meta_lr             = args.meta_lr,
        meta_lr_schedule    = args.lr_schedule,
        inner_steps         = args.inner_steps,
        inner_lr            = args.inner_lr,
        support_episodes    = args.support,
        query_episodes      = args.query,
        save_dir            = args.save_dir,
        save_every          = args.save_every,
        log_every           = args.log_every,
        device              = device,
        compile_model       = not args.no_compile,
    )

    # ── 3. MAMLTrainer ────────────────────────────────────────────────────────
    trainer = MAMLTrainer(dr, config=cfg)

    # Ctrl+C → 현재 체크포인트 저장 후 종료
    def _graceful_exit(sig, frame):
        print("\n[train_v4] 중단 감지 — 체크포인트 저장 중…")
        trainer._save_checkpoint("interrupted")
        print("[train_v4] 저장 완료. 종료.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    # ── 4. 속도 예측 (1 iter warm-run) ───────────────────────────────────────
    print("[train_v4] 속도 측정 중 (1 iteration)…")
    _t_probe_start = time.time()
    cfg_probe = MAMLConfig(
        n_tasks_per_iter=min(args.tasks, 2),
        meta_iterations=1,
        inner_steps=1,
        support_episodes=1,
        query_episodes=1,
        save_dir=args.save_dir,
        save_every=999999,
        log_every=999999,
        device=device,
        compile_model=False,
    )
    _probe_trainer = MAMLTrainer(dr, config=cfg_probe)
    _probe_trainer.train()
    _probe_sec = time.time() - _t_probe_start
    # probe: tasks=2, inner_steps=1, eps=1+1 → scale to actual config
    _scale = (args.tasks / min(args.tasks, 2)) \
             * (args.inner_steps / 1) \
             * ((args.support + args.query) / 2.0)
    _sec_per_iter_estimate = _probe_sec * _scale
    _eta_h = _sec_per_iter_estimate * args.iters / 3600
    print(f"[train_v4] 예상 속도: {_sec_per_iter_estimate:.0f}s/iter  "
          f"총 예상 시간: {_eta_h:.1f}시간  (graph cache 포함)")
    print()

    # ── 5. 학습 시작 ──────────────────────────────────────────────────────────
    t0 = time.time()
    trainer.train(resume_from=args.resume)

    elapsed = time.time() - t0
    h, m = divmod(int(elapsed), 3600)
    m, s = divmod(m, 60)
    print(f"\n[train_v4] 완료  총 소요: {h}h {m}m {s}s")
    print(f"           체크포인트: {args.save_dir}/maml_gnn_final.pt")


if __name__ == "__main__":
    main()
