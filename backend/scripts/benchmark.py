"""Reproducible local allocator + SQLite benchmark. Never calls an AI provider."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.models import ReplanRequest, ScenarioChange, World
from app.seed import seed_world
from app.service import RecoveryService, apply_change
from app.store import Store


def seeded_world(passengers,closed):
    world=seed_world()
    while sum(g.size for g in world.groups)<passengers:
        remaining=passengers-sum(g.size for g in world.groups)
        original=world.groups[len(world.groups)%48]
        size=min(original.size,remaining)
        world.groups.append(original.model_copy(update={"id":f"BENCH{len(world.groups):03}","size":size,"status":"waiting","token":f"synthetic-benchmark-token-{len(world.groups)}","members":[f"Synthetic member {n}" for n in range(size)]}))
    if closed:
        apply_change(world,ScenarioChange(type="airport_closed",target_id="CRA"))
    return world


def distribution(values):
    ordered=sorted(values)
    return {"median":round(statistics.median(ordered),2),"p95":round(ordered[max(0,math.ceil(.95*len(ordered))-1)],2),"min":round(ordered[0],2),"max":round(ordered[-1],2)}


async def benchmark(passengers,samples,closed):
    timings=[];outcomes=[]
    template=seeded_world(passengers,closed)
    with tempfile.TemporaryDirectory(prefix="airport-recovery-benchmark-") as directory:
        for sample in range(samples):
            settings=Settings(database_path=str(Path(directory)/f"{sample}.sqlite3"),_env_file=None)
            store=Store(settings.database_path)
            def install(world):
                cloned=template.model_copy(deep=True)
                for field in World.model_fields:
                    if field!="version":
                        setattr(world,field,getattr(cloned,field))
            store.mutate("benchmark-fixture",{},1,install)
            service=RecoveryService(settings,store)
            started=time.perf_counter()
            result=await service.replan(ReplanRequest(expected_version=2,idempotency_key=f"benchmark-sample-{sample}",mode="solver"))
            elapsed=(time.perf_counter()-started)*1000
            decision=result["decision"]
            timings.append({**decision["timings_ms"],"handler_wall_ms":round(elapsed,2)})
            selected=next(c for c in decision["candidates"] if c["id"]==decision["selected_plan_id"])
            outcomes.append({"recovered_passengers":selected["recovered_passengers"],"exceptions":sum(e["passengers"] for e in selected["exceptions"]),"validated":selected["validation"]["valid"],"candidate_count":len(decision["candidates"])})
    return {"passengers":passengers,"scenario":"CRA_closed" if closed else "initial_cancellation","sample_count":samples,"timings_ms":{key:distribution([s[key] for s in timings]) for key in timings[0]},"outcomes":outcomes,"raw_samples_ms":timings}


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples",type=int,default=12)
    parser.add_argument("--passengers",type=int,nargs="+",default=[180,240,300])
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if not 2<=args.samples<=100 or any(n<180 or n>300 for n in args.passengers):
        parser.error("Use 2–100 samples and passenger counts between 180 and 300")
    processor=platform.processor()
    if sys.platform=="darwin":
        try:
            processor=subprocess.check_output(["sysctl","-n","machdep.cpu.brand_string"],text=True).strip()
        except (OSError,subprocess.SubprocessError):
            pass
    report={"measured_at":datetime.now(timezone.utc).isoformat(),"mode":"deterministic_solver_only","ai_calls":0,"optimality":"feasible heuristic; global optimality not proven","environment":{"python":platform.python_version(),"platform":platform.platform(),"architecture":platform.machine(),"processor":processor,"logical_cpus":os.cpu_count()},"measurement":"In-process service and local SQLite, no HTTP/network/provider latency; warm imports, fresh database per sample; p95 uses nearest rank; server_total excludes telemetry write and response transmission.","cases":[]}
    for passengers in args.passengers:
        for closed in (False,True):
            report["cases"].append(await benchmark(passengers,args.samples,closed))
    encoded=json.dumps(report,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(encoded+"\n")
    print(encoded)


if __name__=="__main__":
    asyncio.run(main())
