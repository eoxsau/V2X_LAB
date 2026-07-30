"""
V4 Universal Policy — A100 Training Entry Point.

Usage (on A100 server):
    cd backend
    python scripts/train_v4.py [options]

Steps this script runs in order:
  1. [PRECACHE]  Build OSM graph cache for 1601 train + 161 holdout regions
  2. [SIONNA]    Pre-compute SINR channel maps (requires sionna + tensorflow)
  3. [LLM]       Generate V2X Q&A dataset and fine-tune Llama 3.1 8B with LoRA
  4. [MAML]      FOMAML training: 10,000 meta-iterations across 1601 regions
  5. [FEDERATED] Federated simulation: 50 virtual FL clients × 100 rounds
  6. [VALIDATE]  N=30 statistical validation on holdout regions

Each step can be run independently via --only <step>.

Expected runtime on A100 80 GB:
  PRECACHE   ~2h   (osmium extraction for 1601 regions)
  SIONNA     ~6h   (ray tracing + SINR map per region)
  LLM        ~4h   (LoRA fine-tuning, 3 epochs, 10K pairs)
  MAML       ~60h  (10K meta-iters × 16 tasks × 5 inner steps)
  FEDERATED  ~8h   (50 clients × 100 FL rounds)
  VALIDATE   ~1h   (N=30 × 161 holdout regions)
"""
import argparse
import sys
import time
from pathlib import Path

# ── Ensure backend/ is on the Python path ─────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.rl.v4.domain_randomizer import DomainRandomizer
from app.services.rl.v4.maml_trainer import MAMLTrainer, MAMLConfig
from app.services.rl.v4.statistical_validator import run_n_seeds

STEPS = ("precache", "sionna", "llm", "maml", "federated", "validate")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="V4 Universal Policy Training Pipeline")
    p.add_argument("--only", choices=STEPS, default=None,
                   help="Run only this step (default: run all)")
    p.add_argument("--skip", choices=STEPS, nargs="+", default=[],
                   help="Skip these steps")
    p.add_argument("--seed",           type=int,   default=42)
    p.add_argument("--device",         type=str,   default="cuda")
    p.add_argument("--meta-iters",     type=int,   default=10_000)
    p.add_argument("--tasks-per-iter", type=int,   default=16)
    p.add_argument("--inner-steps",    type=int,   default=5)
    p.add_argument("--n-fl-rounds",    type=int,   default=100)
    p.add_argument("--n-fl-clients",   type=int,   default=50)
    p.add_argument("--n-validate",     type=int,   default=30)
    p.add_argument("--resume",         type=str,   default=None,
                   help="Path to MAML checkpoint to resume from")
    p.add_argument("--gpu-id",         type=int,   default=0)
    p.add_argument("--skip-sionna",    action="store_true",
                   help="Skip Sionna even if available (use 3GPP model only)")
    p.add_argument("--skip-llm",       action="store_true",
                   help="Skip LLM fine-tuning")
    return p.parse_args()


def should_run(step: str, args: argparse.Namespace) -> bool:
    if args.only and args.only != step:
        return False
    if step in args.skip:
        return False
    return True


# ── Step 1: OSM Graph Cache ────────────────────────────────────────────────────
def step_precache(args: argparse.Namespace) -> None:
    print("\n" + "="*60)
    print("STEP 1: OSM Graph Pre-caching")
    print("="*60)
    t0 = time.time()

    train_dr    = DomainRandomizer(train=True,  seed=args.seed)
    holdout_dr  = DomainRandomizer(train=False, seed=args.seed)

    print(f"Train regions:   {len(train_dr._train_regions)}")
    print(f"Holdout regions: {len(holdout_dr._holdout_regions)}")

    train_dr.pre_cache_all(verbose=True)
    holdout_dr._train = False
    holdout_dr.pre_cache_all(verbose=True)

    print(f"Pre-caching complete in {(time.time()-t0)/60:.1f} min")


# ── Step 2: Sionna Channel Maps ───────────────────────────────────────────────
def step_sionna(args: argparse.Namespace) -> None:
    print("\n" + "="*60)
    print("STEP 2: Sionna Channel Map Pre-computation")
    print("="*60)
    t0 = time.time()

    try:
        from app.services.rl.sionna.channel_precompute import SionnaPrecomputer
    except ImportError as e:
        print(f"[SKIP] Sionna not installed: {e}")
        print("       Install with: pip install sionna tensorflow")
        return

    # Build region task list from cached graphs
    import pickle
    from pathlib import Path
    CACHE_DIR = Path(__file__).parent.parent / "data" / "v4_graph_cache"
    region_tasks = []

    dr = DomainRandomizer(train=True, seed=args.seed)
    for region in dr._train_regions:
        cache_path = CACHE_DIR / f"{region['osm_id']}.pkl"
        if not cache_path.exists():
            continue
        with open(cache_path, "rb") as f:
            graph = pickle.load(f)
        # Synthetic BS positions at high-degree nodes
        nodes = list(graph["nodes"].items())
        adj   = graph["adjacency"]
        hi_deg = sorted(nodes, key=lambda t: -len(adj.get(t[0], [])))[:3]
        bs_pos = [{"id": f"BS-{i}", "lat": d["lat"], "lng": d["lng"]}
                  for i, (nid, d) in enumerate(hi_deg)]
        region_tasks.append({
            "osm_id": region["osm_id"],
            "bbox": {
                "s": region["min_lat"], "n": region["max_lat"],
                "w": region["min_lon"], "e": region["max_lon"],
            },
            "buildings_geojson": "{}",   # optional: load from PostGIS
            "bs_positions": bs_pos,
        })

    pc = SionnaPrecomputer(gpu_id=args.gpu_id)
    pc.run_all_regions(region_tasks, verbose=True)
    print(f"Sionna maps complete in {(time.time()-t0)/60:.1f} min")


# ── Step 3: LLM 연결 확인 (Bedrock / Vertex / Azure) ─────────────────────────
def step_llm(args: argparse.Namespace) -> None:
    print("\n" + "="*60)
    print("STEP 3: LLM API 연결 확인 (provider-agnostic)")
    print("="*60)
    t0 = time.time()

    import os
    provider = os.getenv("LLM_PROVIDER", "auto")
    print(f"[LLM] LLM_PROVIDER={provider}  seed={args.seed}")

    try:
        from app.services.rl.llm.llm_client import chat
        resp = chat("V2X에서 CBR 임계값은? 한 문장으로 답하세요.")
        print(f"[LLM] 응답: {resp[:120]}")
        print(f"[LLM] API 연결 성공 ({(time.time()-t0)*1000:.0f} ms)")
    except Exception as e:
        print(f"[LLM] API 연결 실패: {e}")
        print("      .env의 LLM_PROVIDER / API 키를 확인하세요.")
        print("      학습 파이프라인은 계속 진행됩니다 (LLM은 추론 시 사용).")


# ── Step 4: MAML Training ─────────────────────────────────────────────────────
def step_maml(args: argparse.Namespace) -> None:
    print("\n" + "="*60)
    print("STEP 4: FOMAML Universal Policy Training")
    print("="*60)
    t0 = time.time()

    try:
        import torch
        from torch_geometric.data import Data  # noqa: F401
    except ImportError as e:
        print(f"[ERROR] {e}")
        print("        Install with: pip install torch torch-geometric")
        sys.exit(1)

    cfg = MAMLConfig(
        n_tasks_per_iter=args.tasks_per_iter,
        meta_iterations=args.meta_iters,
        inner_steps=args.inner_steps,
        device=args.device,
        compile_model=(args.device == "cuda"),
    )

    # Load Sionna channel maps if available
    channel_lookup_cache = None
    try:
        from app.services.rl.sionna.channel_lookup import RegionChannelLookupCache
        channel_lookup_cache = RegionChannelLookupCache(max_cached=8)
        print("[MAML] Sionna channel lookup enabled")
    except Exception:
        print("[MAML] No Sionna maps — using 3GPP TR 38.901 path-loss model")

    randomizer = DomainRandomizer(train=True, seed=args.seed)
    trainer = MAMLTrainer(
        randomizer,
        config=cfg,
        channel_lookup=channel_lookup_cache,
    )
    trainer.train(resume_from=args.resume)
    print(f"MAML training complete in {(time.time()-t0)/60:.1f} min")


# ── Step 5: Federated RL ──────────────────────────────────────────────────────
def step_federated(args: argparse.Namespace) -> None:
    print("\n" + "="*60)
    print("STEP 5: Federated RL (Flower FedAvg)")
    print("="*60)
    t0 = time.time()

    try:
        import flwr as fl  # noqa: F401
    except ImportError as e:
        print(f"[SKIP] flwr not installed: {e}")
        print("       Install with: pip install flwr")
        return

    from app.services.rl.federated.fl_client import simulate_federation

    # Find the latest MAML checkpoint to use as initial weights
    SAVE_DIR = Path(__file__).parent.parent / "app" / "services" / "rl" / "models"
    checkpoints = sorted(SAVE_DIR.glob("maml_gnn_*.pt"))
    initial = str(checkpoints[-1]) if checkpoints else None
    if initial:
        print(f"[FL] Initial weights from: {initial}")

    simulate_federation(
        n_clients=args.n_fl_clients,
        n_rounds=args.n_fl_rounds,
        device=args.device,
    )
    print(f"Federated RL complete in {(time.time()-t0)/60:.1f} min")


# ── Step 6: Statistical Validation ───────────────────────────────────────────
def step_validate(args: argparse.Namespace) -> None:
    print("\n" + "="*60)
    print("STEP 6: N=30 Statistical Validation on Holdout Regions")
    print("="*60)
    t0 = time.time()

    try:
        import torch
        from app.services.rl.v4.universal_gnn_policy import UniversalGNNPolicy
    except ImportError as e:
        print(f"[SKIP] {e}")
        return

    # Load the best MAML model
    SAVE_DIR = Path(__file__).parent.parent / "app" / "services" / "rl" / "models"
    checkpoints = sorted(SAVE_DIR.glob("maml_gnn_*.pt"))
    if not checkpoints:
        print("[VALIDATE] No MAML checkpoint found. Run --only maml first.")
        return

    ckpt_path = checkpoints[-1]
    print(f"[VALIDATE] Loading model: {ckpt_path}")
    model = UniversalGNNPolicy()
    ckpt = torch.load(str(ckpt_path), map_location="cpu")
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    holdout_dr = DomainRandomizer(train=False, seed=args.seed)

    @torch.no_grad()
    def policy_fn(obs
    ):
        data = obs.to("cpu") if hasattr(obs, "to") else obs
        logits, _ = model(data)
        return int(logits.argmax().item())

    result = run_n_seeds(
        policy_fn=policy_fn,
        holdout_randomizer=holdout_dr,
        n=args.n_validate,
        verbose=True,
    )

    print("\n" + "="*60)
    print("VALIDATION RESULTS")
    print("="*60)
    summary = result.summary()
    for k, v in summary.items():
        print(f"  {k:35s}: {v}")

    # Save results
    import json
    out = SAVE_DIR / "validation_results.json"
    with open(out, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\n[VALIDATE] Results saved → {out}")
    print(f"Validation complete in {(time.time()-t0)/60:.1f} min")


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    args = parse_args()
    print("V4 Universal Policy Training Pipeline")
    print(f"Seed: {args.seed} | Device: {args.device}")
    if args.only:
        print(f"Running only: {args.only}")
    if args.skip:
        print(f"Skipping: {args.skip}")

    t_total = time.time()

    if should_run("precache", args):
        step_precache(args)

    if should_run("sionna", args) and not args.skip_sionna:
        step_sionna(args)

    if should_run("llm", args) and not args.skip_llm:
        step_llm(args)

    if should_run("maml", args):
        step_maml(args)

    if should_run("federated", args):
        step_federated(args)

    if should_run("validate", args):
        step_validate(args)

    print(f"\nTotal pipeline: {(time.time()-t_total)/60:.1f} min")
    print("Done.")


if __name__ == "__main__":
    main()
