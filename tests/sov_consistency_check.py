"""
Manual diagnostic script: verify SOV consistency across multiple runs.

Usage:
  python tests/sov_consistency_check.py

Pass criteria:
  - 3 runs of same input show SOV variance < 30 percentage points
  - All runs return ai_brand_awareness consistently

Not part of CI — run manually before major prompt changes.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mvp_b_prompt.logic import run_analysis  # noqa: E402

BRAND = "Bambu Lab"
INDUSTRY = "消费电子 / 3D 打印机"
COMPETITORS = ["Prusa Research", "Creality", "Flsun3D"]
PERSPECTIVE = "B2C 消费者"
N_RUNS = 3


def main() -> int:
    runs: list[dict] = []
    for i in range(1, N_RUNS + 1):
        print(f"\n{'=' * 70}")
        print(f"  RUN {i} of {N_RUNS}")
        print(f"{'=' * 70}")
        t0 = time.monotonic()
        try:
            result = run_analysis(BRAND, INDUSTRY, COMPETITORS, PERSPECTIVE)
        except Exception:
            print(f"[FATAL] run {i} crashed:\n{traceback.format_exc()}")
            runs.append({"sov": None, "awareness": None, "opps": None, "cost": None})
            continue
        elapsed = int(time.monotonic() - t0)
        sov = result["sov"].get(BRAND)
        awareness = result["ai_brand_awareness"]
        opps = len(result["opportunities"])
        cost = result["estimated_cost_usd"]
        print(f"\n  [run {i} done in {elapsed}s]")
        print(f"  user_sov            = {sov}%")
        print(f"  ai_brand_awareness  = {awareness}")
        print(f"  opportunities_count = {opps}")
        print(f"  cost                = ${cost:.4f}")
        runs.append({"sov": sov, "awareness": awareness, "opps": opps, "cost": cost})

    print("\n" + "=" * 70)
    print("  CONSISTENCY SUMMARY")
    print("=" * 70)
    print(f"  Brand:       {BRAND}")
    print(f"  Competitors: {', '.join(COMPETITORS)}")
    print()
    print(f"  {'Run':<5} {'SOV':>6} {'Awareness':<10} {'Opps':>5} {'Cost':>9}")
    print(f"  {'-' * 5} {'-' * 6} {'-' * 10} {'-' * 5} {'-' * 9}")
    for i, r in enumerate(runs, start=1):
        sov_str = f"{r['sov']}%" if r["sov"] is not None else "—"
        aw = r["awareness"] or "—"
        opps = r["opps"] if r["opps"] is not None else "—"
        cost = f"${r['cost']:.4f}" if r["cost"] is not None else "—"
        print(f"  {i:<5} {sov_str:>6} {aw:<10} {str(opps):>5} {cost:>9}")

    sovs = [r["sov"] for r in runs if r["sov"] is not None]
    if len(sovs) >= 2:
        spread = max(sovs) - min(sovs)
        all_in_band = all(60 <= s <= 100 for s in sovs)
        print()
        print(f"  SOV spread:  {spread} percentage points  (max {max(sovs)} − min {min(sovs)})")
        print(f"  All in 60-100% band:  {all_in_band}")
        if spread > 30:
            print(f"  ⚠️  DRIFT > 30 pp — UI should disclose LLM variance")
        elif all_in_band:
            print(f"  ✅ Stable in expected band — tool is consistent")
        else:
            print(f"  ⚠️  Stable spread but some runs outside 60-100% band — review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
