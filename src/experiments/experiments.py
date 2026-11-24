#!/usr/bin/env python3
"""Reproducible crossbar regression and bounded sweep driver."""
import argparse
import csv
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import subprocess
import sys
import time
import textwrap
import uuid

SCHEMA = 1
POLICY = {"version": 1, "rtol": 1e-6, "atol_ns": 1e-6, "atol_GBps": 1e-6}
HERE = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_GEM5 = HERE.parent / "gem5/build/Garnet_standalone/gem5.opt"
SIM = HERE / "src/experiments/heterogeneous_architecture/run_dsp.py"
SOURCE_FILES = [SIM, HERE / "src/experiments/heterogeneous_architecture/experiment_config.py",
                HERE / "src/experiments/heterogeneous_architecture/interconnects.py",
                HERE / "src/experiments/heterogeneous_architecture/memory_targets.py",
                HERE / "src/experiments/heterogeneous_architecture/workloads.py",
                HERE / "src/experiments/heterogeneous_architecture/metrics.py",
                HERE / "src/configuration/configurationA/synthetic_initiator_base.cc",
                HERE / "src/experiments/experiments.py"]


def load(path):
    with open(path, encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def finite_json(value):
    if isinstance(value, float) and not math.isfinite(value): return None
    if isinstance(value, dict): return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, list): return [finite_json(v) for v in value]
    return value


def config_id(config):
    return hashlib.sha256(("crossbar-config-v1\n" + canonical(config)).encode()).hexdigest()[:16]


def resolved_inputs(manifest):
    """Return only behavior inputs; paths/provenance are deliberately excluded."""
    result = json.loads(canonical(manifest))
    architecture=result.setdefault("architecture","crossbar")
    if architecture == "chiplet":
        raise ValueError("architecture=chiplet is unsupported/not implemented")
    if architecture != "crossbar":
        raise ValueError(f"unsupported architecture: {architecture}")
    result.setdefault("configuration", "config_a")
    result.setdefault("workload", {"type": "synthetic"})
    result.setdefault("memory", "simple")
    result.setdefault("connection", "xbar")
    result.setdefault("mem_size", "16MiB")
    result.setdefault("simple_latency", "30ns")
    result.setdefault("simple_bandwidth", "12.8GiB/s")
    result.setdefault("sys_clock", "1GHz")
    result.setdefault("cache_line_size", 64)
    result.setdefault("timeout", "10ms")
    result.setdefault("interconnect", {})
    result["interconnect"] = {"width": 16, "frontend_latency": 1, "forward_latency": 1,
                              "response_latency": 1, **result["interconnect"]}
    result.setdefault("initiators", {})
    result.setdefault("num_blocks", 20)
    result["measurement_policy"] = "workload_window_v1"
    return result


def git_revision(repo):
    try:
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def source_identity():
    h = hashlib.sha256()
    for path in SOURCE_FILES:
        h.update(str(path.relative_to(HERE)).encode()); h.update(b"\0")
        if path.exists(): h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def run_one(manifest, gem5, out, timeout):
    out.mkdir(parents=True, exist_ok=False)
    config = resolved_inputs(manifest)
    cid = config_id(config)
    manifest_path = out / "input_manifest.json"
    manifest_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    cmd = [str(pathlib.Path(gem5).resolve()), f"--outdir={out.resolve()}", str(SIM),
           f"--config={manifest_path.resolve()}", f"--summary={out.resolve() / 'dsp_summary.json'}",
           f"--metrics-json={out.resolve() / 'memory_metrics.json'}",
           f"--metrics-csv={out.resolve() / 'memory_metrics.csv'}"]
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=timeout)
        code = proc.returncode
        output = proc.stdout
    except subprocess.TimeoutExpired as exc:
        timed_out, code = True, 124
        output = (exc.stdout or "")
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        output += f"\nWall timeout after {timeout} seconds\n"
    elapsed = time.monotonic() - start
    (out / "run.log").write_text(output)
    gem5_path = pathlib.Path(gem5).resolve()
    provenance = {"schema_version": SCHEMA, "run_id": out.name, "configuration_id": cid,
                  "command": cmd, "cwd": str(HERE), "wall_seconds": elapsed,
                  "wall_timeout": timed_out, "exit_code": code,
                  "source_revision": git_revision(HERE), "gem5_revision": git_revision(HERE.parent / "gem5"),
                  "source_code_sha256": source_identity(),
                  "gem5_binary": str(gem5_path),
                  "gem5_binary_sha256": hashlib.sha256(gem5_path.read_bytes()).hexdigest() if gem5_path.exists() else None,
                  "host": platform.platform(), "python": sys.version}
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    summaries = {}
    for role in ("dsp", "npu", "mcu"):
        p = out / f"{role}_summary.json"
        if p.exists():
            summaries[role] = load(p)
    complete = (code == 0 and len(summaries) == 3 and
                all(s.get("verification_passed") == 1 and
                    s.get("requests", {}).get("accepted") == s.get("requests", {}).get("completed")
                    for s in summaries.values()))
    agg = {"requests": {}, "payload": {}}
    for summary in summaries.values():
        for section in agg:
            for key, value in summary.get(section, {}).items():
                if section == "payload" and key.endswith("bandwidth_Bps"):
                    continue
                if isinstance(value, (int, float)) and math.isfinite(value):
                    agg[section][key] = agg[section].get(key, 0) + value
    ticks = max((s.get("measurement", {}).get("elapsed_ticks", 0) for s in summaries.values()), default=0)
    payload_bytes = agg["payload"].get("bytes_read", 0) + agg["payload"].get("bytes_written", 0)
    tick_match = re.search(r"Global frequency set at ([0-9.eE+-]+) ticks per second", output)
    ticks_per_second = float(tick_match.group(1)) if tick_match else None
    complete = (complete and ticks > 0 and ticks_per_second is not None and ticks_per_second > 0
                and "bytes_read" in agg["payload"] and "bytes_written" in agg["payload"]
                and all(math.isfinite(s.get("latency_ticks", {}).get(k, float("nan")))
                        for s in summaries.values() for k in ("mean_read", "mean_write")))
    latency_ticks = []
    for role, summary in summaries.items():
        for kind, count_key in (("read", "work_reads_completed"), ("write", "work_writes_completed")):
            count = summary.get("requests", {}).get(count_key, 0)
            mean = summary.get("latency_ticks", {}).get("mean_" + kind)
            if count and mean is not None:
                latency_ticks.extend([mean] * count)
    final_outstanding = {r: s.get("requests", {}).get("accepted", 0) - s.get("requests", {}).get("completed", 0)
                         for r, s in summaries.items()}
    memory_metrics_path=out/"memory_metrics.json"
    memory_systems=finite_json(load(memory_metrics_path).get("memory_systems",{})) if memory_metrics_path.exists() else {}
    result = {"schema_version": SCHEMA, "run_id": out.name, "configuration_id": cid,
              "configuration": config, "architecture": config["architecture"],
              "workload_id": config.get("workload", {}).get("type", "synthetic"),
              "backend_id": config.get("memory", "simple"),
              "seed": {role: summary.get("parameters", {}).get("payload_seed")
                      for role, summary in summaries.items()},
              "provenance_path": str((out / "provenance.json").resolve()), "raw_paths": {"stats": "stats.txt", "config_ini": "config.ini",
              "config_json": "config.json", "log": "run.log"}, "exit_status": code,
              "exit_reason": next((line.partition("cause=")[2] for line in output.splitlines() if "DSP prototype exit:" in line), None),
              "native_verification": {r: s.get("verification_passed") for r, s in summaries.items()},
              "simulation_passed": complete, "regression_status": "not_baselined",
              "initiators": summaries, "memory_systems": memory_systems,
              "aggregate": {"requests": agg["requests"], "payload": agg["payload"],
              "completion_ticks": ticks, "tick_frequency_hz": ticks_per_second,
              "duration_seconds": ticks / ticks_per_second if ticks and ticks_per_second else None,
              "throughput_GBps": payload_bytes * ticks_per_second / ticks / 1e9 if ticks and ticks_per_second else None,
              "mean_latency_ticks": sum(latency_ticks) / len(latency_ticks) if latency_ticks else None,
              "final_outstanding": final_outstanding,
              "measurement_window": "existing workload window: first accepted work read through final work write response; excludes initialization and verification"},
              "units": {"payload": "bytes", "throughput_GBps": "decimal GB/s", "latency": "ticks"},
              "metrics_missing": {"generated_request_count": "generator does not expose an independent generated count"}}
    (out / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def compare(reference, actual):
    errors = []
    if reference.get("configuration_id") != actual.get("configuration_id"):
        errors.append(("configuration_id", reference.get("configuration_id"), actual.get("configuration_id")))
    rp = load(pathlib.Path(reference["provenance_path"])) if isinstance(reference.get("provenance_path"), str) and pathlib.Path(reference["provenance_path"]).exists() else None
    ap = load(pathlib.Path(actual["provenance_path"])) if isinstance(actual.get("provenance_path"), str) and pathlib.Path(actual["provenance_path"]).exists() else None
    if rp and ap:
        rbuild = rp.get("gem5_revision") or rp.get("gem5_commit")
        abuild = ap.get("gem5_revision") or ap.get("gem5_commit")
        if rbuild != abuild:
            errors.append(("gem5_revision", rbuild, abuild))
        if rp.get("gem5_binary_sha256") and ap.get("gem5_binary_sha256") and rp["gem5_binary_sha256"] != ap["gem5_binary_sha256"]:
            errors.append(("gem5_binary_sha256", rp["gem5_binary_sha256"], ap["gem5_binary_sha256"]))
    diffs = []
    def check(path, a, b, exact=False, unit=""):
        if a is None or b is None or not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or not math.isfinite(a) or not math.isfinite(b):
            diffs.append({"metric": path, "reference": a, "actual": b, "status": "FAIL", "reason": "missing or non-finite required metric"}); return
        atol = (POLICY["atol_ns"] if unit == "ns" else
                POLICY["atol_GBps"] if unit == "GB/s" else
                POLICY["atol_GBps"] * 1e9 if unit == "B/s" else 0)
        tolerance = 0 if exact else atol + POLICY["rtol"] * abs(a)
        delta = abs(b-a)
        diffs.append({"metric": path, "reference": a, "actual": b, "absolute_change": delta,
                      "relative_change": delta / abs(a) if a else (0 if delta == 0 else None),
                      "tolerance": tolerance, "status": "PASS" if delta <= tolerance else "FAIL"})
    if reference.get("simulation_passed") is not True or actual.get("simulation_passed") is not True:
        errors.append(("simulation_passed", reference.get("simulation_passed"), actual.get("simulation_passed")))
    if reference.get("seed") != actual.get("seed"):
        errors.append(("seed", reference.get("seed"), actual.get("seed")))
    def walk_native(obj, path="", unit=""):
        if isinstance(obj, dict):
            if "supported" in obj:
                yield path+".supported", obj.get("supported"), "status", ""
                if obj.get("supported") and obj.get("value") is not None:
                    yield from walk_native(obj["value"], path+".value", obj.get("unit", ""))
                return
            for k,v in obj.items(): yield from walk_native(v, f"{path}.{k}" if path else k, unit)
        elif isinstance(obj, list):
            for i,v in enumerate(obj): yield from walk_native(v,f"{path}[{i}]",unit)
        elif isinstance(obj,(int,float)) or obj is None:
            yield path,obj,"value",unit
    rnative, anative = dict((p,(v,k,u)) for p,v,k,u in walk_native(reference.get("memory_systems",{}))), dict((p,(v,k,u)) for p,v,k,u in walk_native(actual.get("memory_systems",{})))
    if set(rnative) != set(anative): errors.append(("memory_system_metrics", sorted(rnative), sorted(anative)))
    for path in sorted(set(rnative)&set(anative)):
        rv,rkind,runit=rnative[path]; av,akind,aunit=anative[path]
        if rkind == "status" or akind == "status":
            if rv != av: errors.append(("memory."+path,rv,av))
        elif rv is not None or av is not None:
            if isinstance(rv,(int,float)) and isinstance(av,(int,float)):
                unit=runit or aunit
                check("memory."+path,rv,av,exact=isinstance(rv,int) and isinstance(av,int),unit=unit)
            elif rv != av:
                errors.append(("memory."+path,rv,av))
    for role in ("dsp", "npu", "mcu"):
        rs, ac = reference.get("initiators", {}).get(role), actual.get("initiators", {}).get(role)
        if not rs or not ac:
            errors.append((f"initiators.{role}", bool(rs), bool(ac))); continue
        for section in ("requests", "payload"):
            for metric, value in rs.get(section, {}).items():
                if isinstance(value, (int, float)):
                    actual_value = ac.get(section, {}).get(metric)
                    check(f"{role}.{section}.{metric}", value, actual_value,
                          exact=isinstance(value, int) and isinstance(actual_value, int),
                          unit="GB/s" if metric.endswith("GBps") else "B/s" if metric.endswith("Bps") else "")
        for metric in ("elapsed_ticks",):
            check(f"{role}.measurement.{metric}", rs.get("measurement", {}).get(metric), ac.get("measurement", {}).get(metric), exact=True)
        for metric in ("mean_read", "mean_write", "max_read", "max_write"):
            rvalue, avalue = rs.get("latency_ticks", {}).get(metric), ac.get("latency_ticks", {}).get(metric)
            rhz = reference.get("aggregate", {}).get("tick_frequency_hz")
            ahz = actual.get("aggregate", {}).get("tick_frequency_hz")
            if rvalue is not None and avalue is not None and rhz and ahz:
                r_ns, a_ns = rvalue / rhz * 1e9, avalue / ahz * 1e9
                check(f"{role}.latency_ns.{metric}", r_ns, a_ns,
                      exact=isinstance(rvalue, int) and isinstance(avalue, int))
            else:
                check(f"{role}.latency_ns.{metric}", None, None)
        rfinal = rs.get("requests", {}).get("accepted", 0) - rs.get("requests", {}).get("completed", 0)
        afinal = ac.get("requests", {}).get("accepted", 0) - ac.get("requests", {}).get("completed", 0)
        check(f"{role}.final_outstanding", rfinal, afinal, exact=True)
        if rs.get("verification_passed") != ac.get("verification_passed"):
            errors.append((f"{role}.verification_passed", rs.get("verification_passed"), ac.get("verification_passed")))
    return {"policy": POLICY, "comparison_errors": errors, "metrics": diffs,
            "passed": not errors and all(d["status"] == "PASS" for d in diffs)}


def set_dotted(obj, key, value):
    parts = key.split(".")
    cur = obj
    for part in parts[:-1]:
        if part not in cur and part == "interconnect":
            cur[part] = {"width": 16, "frontend_latency": 1, "forward_latency": 1, "response_latency": 1}
        if part not in cur and part == "initiators":
            cur[part] = {}
        if part == "npu" and cur is not None and isinstance(cur, dict):
            cur.setdefault(part, {})
        if part not in cur or not isinstance(cur[part], dict):
            raise ValueError(f"unsupported sweep parameter: {key}")
        cur = cur[part]
    if parts[-1] not in cur:
        if key == "initiators.npu.max_outstanding":
            cur[parts[-1]] = 8
        else:
            raise ValueError(f"unsupported sweep parameter: {key}")
    cur[parts[-1]] = value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    for action in ("run", "record-reference", "check"):
        q = sub.add_parser(action); q.add_argument("--manifest", required=True); q.add_argument("--gem5", default=str(DEFAULT_GEM5)); q.add_argument("--output", required=True)
        if action == "check": q.add_argument("--reference", required=True)
        q.add_argument("--timeout", type=float, default=120)
    q = sub.add_parser("sweep"); q.add_argument("--manifest", required=True); q.add_argument("--gem5", default=str(DEFAULT_GEM5)); q.add_argument("--output", required=True); q.add_argument("--parameter", required=True); q.add_argument("--values", required=True); q.add_argument("--dry-run", action="store_true"); q.add_argument("--timeout", type=float, default=120)
    q = sub.add_parser("report"); q.add_argument("--results", required=True); q.add_argument("--output", required=True)
    q = sub.add_parser("resume"); q.add_argument("--manifest", required=True); q.add_argument("--gem5", default=str(DEFAULT_GEM5)); q.add_argument("--output-root", required=True); q.add_argument("--timeout", type=float, default=120)
    q = sub.add_parser("regression"); q.add_argument("--suite", required=True); q.add_argument("--gem5", default=str(DEFAULT_GEM5)); q.add_argument("--output-root", required=True); q.add_argument("--timeout", type=float, default=120)
    args = p.parse_args()
    if args.action in ("run", "record-reference", "check"):
        result = run_one(load(args.manifest), args.gem5, pathlib.Path(args.output), args.timeout)
        if args.action == "record-reference":
            result["regression_status"] = "reference"
            (pathlib.Path(args.output) / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
            target = pathlib.Path(args.output).with_name(pathlib.Path(args.output).name + "-reference.json")
            target.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n"); print(target)
            return 0 if result["simulation_passed"] else 1
        if args.action == "check":
            ref = load(args.reference); diff = compare(ref, result); dest = pathlib.Path(args.output) / "regression.json"
            result["regression_status"] = "regression_passed" if diff["passed"] else "regression_failed"
            (pathlib.Path(args.output) / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
            dest.write_text(json.dumps(diff, indent=2) + "\n")
            failed = sum(item["status"] != "PASS" for item in diff["metrics"])
            print(f"regression {'PASS' if diff['passed'] else 'FAIL'}: {len(diff['metrics'])-failed}/{len(diff['metrics'])} metrics passed; {len(diff['comparison_errors'])} compatibility errors; report={dest}")
            return 0 if diff["passed"] else 1
        print(json.dumps({"configuration_id": result["configuration_id"], "simulation_passed": result["simulation_passed"], "output": args.output}, indent=2)); return 0 if result["simulation_passed"] else 1
    if args.action == "sweep":
        base = resolved_inputs(load(args.manifest)); values = json.loads(args.values)
        if not isinstance(values, list) or not values: raise ValueError("--values must be a nonempty JSON list")
        allowed = {"interconnect.width", "initiators.npu.max_outstanding"}
        if args.parameter not in allowed:
            raise ValueError("supported sweep parameters are interconnect.width and initiators.npu.max_outstanding")
        nominal = (base["interconnect"]["width"] if args.parameter == "interconnect.width"
                   else base["initiators"].get("npu", {}).get("max_outstanding", 8))
        if nominal not in values: values.append(nominal)
        points = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{args.parameter} values must be positive integers")
            candidate = json.loads(canonical(base)); set_dotted(candidate, args.parameter, value); cid = config_id(candidate)
            if cid not in [pnt["id"] for pnt in points]: points.append({"id": cid, "value": value, "manifest": candidate})
        print(f"{len(points)} unique simulations")
        for point in points: print(f"{args.parameter}={point['value']} id={point['id']}")
        if args.dry_run: return 0
        root = pathlib.Path(args.output); root.mkdir(parents=True, exist_ok=False); rows=[]
        for point in points:
            result = run_one(point["manifest"], args.gem5, root / point["id"], args.timeout)
            result["sweep_parameter"], result["sweep_value"] = args.parameter, point["value"]
            (root / point["id"] / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
            row={"parameter":args.parameter,"value":point["value"],"configuration_id":point["id"],"simulation_passed":result["simulation_passed"],"regression_status":"not_baselined","throughput_GBps":result["aggregate"]["throughput_GBps"],"mean_latency_ticks":result["aggregate"]["mean_latency_ticks"],"completion_ticks":result["aggregate"]["completion_ticks"],"output":point["id"]}
            for role, summary in result["initiators"].items():
                row[role+"_mean_read_ticks"] = summary.get("latency_ticks",{}).get("mean_read")
                row[role+"_mean_write_ticks"] = summary.get("latency_ticks",{}).get("mean_write")
                counts=summary.get("requests",{})
                read_count=counts.get("work_reads_completed",0); write_count=counts.get("work_writes_completed",0)
                lat=summary.get("latency_ticks",{})
                total=read_count+write_count
                row[role+"_mean_latency_ticks"] = ((read_count*(lat.get("mean_read") or 0)+write_count*(lat.get("mean_write") or 0))/total if total else None)
            rows.append(row)
        with (root/"sweep.csv").open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
        (root/"sweep.json").write_text(json.dumps({"schema_version":SCHEMA,"parameter":args.parameter,"results":rows},indent=2)+"\n")
        memory=base["memory"]+(f", {base['simple_latency']} SimpleMemory" if base["memory"]=="simple" else "")
        fixed=f"synthetic DSP/NPU/MCU; {memory}; {base['sys_clock']}; {base['num_blocks']} blocks; fixed role profiles"
        title=f"Completed payload throughput (decimal GB/s) | {fixed}"
        write_svg(root/"throughput.svg", rows, "throughput_GBps", title, args.parameter)
        write_latency_svg(root/"mean_latency.svg", rows, f"Per-initiator mean response latency (ticks) | {fixed}", args.parameter)
        write_raster_plots(root, rows, fixed, args.parameter)
        return 0 if all(r["simulation_passed"] for r in rows) else 1
    if args.action == "report":
        paths=[pathlib.Path(x) for x in json.loads(args.results)]; rows=[load(x) for x in paths]
        out=pathlib.Path(args.output); out.mkdir(parents=True,exist_ok=True)
        fields=["run_id","configuration_id","simulation_passed","regression_status","backend_id","throughput_GBps"]
        with (out/"summary.csv").open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows([{k:r.get(k) for k in fields} for r in rows])
        write_svg(out/"summary.svg",[{"value":r.get("configuration_id"),"simulation_passed":r.get("simulation_passed"),"throughput_GBps":r.get("aggregate",{}).get("throughput_GBps"),"mean_latency_ticks":r.get("aggregate",{}).get("mean_latency_ticks")} for r in rows])
        return 0
    if args.action == "resume":
        config=resolved_inputs(load(args.manifest)); cid=config_id(config); root=pathlib.Path(args.output_root)
        binary=pathlib.Path(args.gem5).resolve(); binary_hash=hashlib.sha256(binary.read_bytes()).hexdigest()
        identity=source_identity(); revision=git_revision(HERE); grev=git_revision(HERE.parent/"gem5")
        if root.exists():
            for result_path in root.rglob("results.json"):
                try:
                    result=load(result_path); prov=load(result_path.parent/"provenance.json")
                except (OSError,ValueError): continue
                if (result.get("configuration_id")==cid and result.get("simulation_passed") is True
                    and prov.get("gem5_binary_sha256")==binary_hash and prov.get("source_code_sha256")==identity
                    and prov.get("source_revision")==revision and prov.get("gem5_revision")==grev):
                    print(f"reused successful matching result: {result_path.parent}"); return 0
        root.mkdir(parents=True,exist_ok=True); out=root/(cid+"-"+uuid.uuid4().hex[:8])
        result=run_one(config,args.gem5,out,args.timeout)
        print(f"created fresh result: {out} (simulation_passed={result['simulation_passed']})")
        return 0 if result["simulation_passed"] else 1
    if args.action == "regression":
        suite_path=pathlib.Path(args.suite).resolve(); suite=load(suite_path)
        cases=suite.get("cases")
        if not isinstance(cases,list) or not cases: raise ValueError("suite must contain a nonempty cases list")
        root=pathlib.Path(args.output_root); root.mkdir(parents=True,exist_ok=False); reports=[]
        for case in cases:
            name=case["id"]; manifest_path=(suite_path.parent/case["manifest"]).resolve(); ref_path=(suite_path.parent/case["reference"]).resolve()
            result=run_one(load(manifest_path),args.gem5,root/name,args.timeout); diff=compare(load(ref_path),result)
            result["regression_status"]="regression_passed" if diff["passed"] else "regression_failed"
            (root/name/"results.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
            (root/name/"regression.json").write_text(json.dumps(diff,indent=2)+"\n")
            reports.append({"case":name,"configuration_id":result["configuration_id"],"simulation_passed":result["simulation_passed"],"regression_status":result["regression_status"],"failed_metrics":sum(m["status"]!="PASS" for m in diff["metrics"]),"comparison_errors":diff["comparison_errors"],"output":name})
        (root/"regression-suite.json").write_text(json.dumps({"schema_version":SCHEMA,"policy":POLICY,"cases":reports,"passed":all(r["regression_status"]=="regression_passed" for r in reports)},indent=2)+"\n")
        for r in reports: print(f"{r['case']}: {r['regression_status']} ({r['failed_metrics']} failed metrics)")
        return 0 if all(r["regression_status"]=="regression_passed" for r in reports) else 1


def write_svg(path, rows, metric="throughput_GBps", title="Completed payload throughput (decimal GB/s)", x_title="parameter"):
    valid=[r for r in rows if r.get("simulation_passed") and isinstance(r.get(metric),(int,float))]
    width,height=800,420; left,bottom=70,350; top=50; right=770
    vals=[r[metric] for r in valid]; lo=min(vals,default=0); hi=max(vals,default=1); hi=hi if hi>lo else lo+1
    pts=[]
    for i,row in enumerate(valid):
        x=left+(right-left)*(i/(max(1,len(valid)-1))); y=bottom-(bottom-top)*(row[metric]-lo)/(hi-lo); pts.append(f"{x:.1f},{y:.1f}")
    labels="".join(f'<text x="{left+(right-left)*(i/(max(1,len(valid)-1))):.1f}" y="375" text-anchor="middle" font-size="11">{row.get("value",i)}</text>' for i,row in enumerate(valid))
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/><text x="400" y="25" text-anchor="middle" font-family="sans-serif">{title}</text><path d="M{left},{top} V{bottom} H{right}" stroke="black" fill="none"/><polyline points="{" ".join(pts)}" fill="none" stroke="#1769aa" stroke-width="2"/>{labels}<text x="18" y="210" transform="rotate(-90 18 210)" font-size="12">{metric}</text><text x="400" y="395" text-anchor="middle" font-size="12">{x_title}</text><text x="400" y="412" text-anchor="middle" font-size="10">Failed/incomplete points excluded; see CSV for status</text></svg>\n',encoding="utf-8")


def write_latency_svg(path, rows, title, x_title):
    roles=("dsp","npu","mcu"); colors=("#1769aa","#d35400","#27823b")
    valid=[r for r in rows if r.get("simulation_passed")]
    vals=[r[f"{role}_mean_latency_ticks"] for r in valid for role in roles if isinstance(r.get(f"{role}_mean_latency_ticks"),(int,float))]
    lo=min(vals,default=0); hi=max(vals,default=1); hi=hi if hi>lo else lo+1
    left,right,top,bottom=70,770,50,350; width,height=800,420; polylines=[]; legends=[]
    for role,color in zip(roles,colors):
        pts=[]
        for i,row in enumerate(valid):
            value=row.get(f"{role}_mean_latency_ticks")
            if not isinstance(value,(int,float)): continue
            x=left+(right-left)*(i/max(1,len(valid)-1)); y=bottom-(bottom-top)*(value-lo)/(hi-lo); pts.append(f"{x:.1f},{y:.1f}")
        polylines.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="2"/>')
        legends.append(f'<text x="{90+100*len(legends)}" y="45" fill="{color}" font-size="12">{role}</text>')
    labels="".join(f'<text x="{left+(right-left)*(i/max(1,len(valid)-1)):.1f}" y="375" text-anchor="middle" font-size="11">{r.get("value",i)}</text>' for i,r in enumerate(valid))
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/><text x="400" y="25" text-anchor="middle" font-family="sans-serif">{title}</text>{"".join(legends)}<path d="M{left},{top} V{bottom} H{right}" stroke="black" fill="none"/>{"".join(polylines)}{labels}<text x="18" y="210" transform="rotate(-90 18 210)" font-size="12">ticks</text><text x="400" y="395" text-anchor="middle" font-size="12">{x_title}</text><text x="400" y="412" text-anchor="middle" font-size="10">Failed/incomplete points excluded; see CSV for status</text></svg>\n',encoding="utf-8")


def write_raster_plots(root, rows, fixed, x_title):
    """Write shareable PNG/PDF plots when the existing host matplotlib is available."""
    try:
        os.environ.setdefault("MPLCONFIGDIR", str(pathlib.Path("/private/tmp/crossbar-mplconfig")))
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ValueError("PNG/PDF plots require matplotlib; SVG plots and CSV remain available") from exc
    valid=[r for r in rows if r.get("simulation_passed")]
    x=[float(r["value"]) for r in valid]
    fig,ax=plt.subplots(figsize=(8,4.5))
    ax.plot(x,[r["throughput_GBps"] for r in valid],marker="o")
    ax.set(xlabel=x_title,ylabel="Completed payload throughput (decimal GB/s)")
    fig.suptitle("Crossbar sweep —\n"+textwrap.fill(fixed,72),fontsize=10,y=.99)
    ax.grid(True,alpha=.25); fig.tight_layout(rect=(0,0,1,.86)); fig.savefig(root/"throughput.png",dpi=160); fig.savefig(root/"throughput.pdf"); plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4.5))
    for role in ("dsp","npu","mcu"):
        values=[r.get(role+"_mean_latency_ticks") for r in valid]
        ax.plot(x,values,marker="o",label=role.upper())
    ax.set(xlabel=x_title,ylabel="Mean response latency (ticks)")
    fig.suptitle("Per-initiator latency —\n"+textwrap.fill(fixed,72),fontsize=10,y=.99)
    ax.grid(True,alpha=.25); ax.legend(); fig.tight_layout(rect=(0,0,1,.86)); fig.savefig(root/"mean_latency.png",dpi=160); fig.savefig(root/"mean_latency.pdf"); plt.close(fig)


if __name__ == "__main__":
    try: sys.exit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr); sys.exit(2)
