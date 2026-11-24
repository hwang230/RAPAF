"""JSON manifest loading and compatibility normalization."""

import json

from workloads import CONFIGURATIONS, KERNELS, ROLES, WORKLOADS


SCALAR_FIELDS = {
    "memory", "connection", "mem_size", "simple_latency",
    "simple_bandwidth", "sys_clock", "cache_line_size", "timeout",
    "summary", "metrics_json", "metrics_csv", "num_blocks",
}


def load_manifest(path):
    with open(path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    if not isinstance(manifest, dict):
        raise ValueError("experiment config must contain a JSON object")
    return manifest


def _legacy_agents(agents):
    """Translate historical workload-as-agent manifests to the new model."""
    if isinstance(agents, str):
        agents = [name.strip() for name in agents.split(",") if name.strip()]
    if isinstance(agents, (list, tuple)):
        agents = {name: {} for name in agents}
    if not isinstance(agents, dict) or not agents:
        raise ValueError("legacy agents must be a nonempty list, string, or object")
    if any(not isinstance(value, dict) for value in agents.values()):
        raise ValueError("legacy agent settings must be objects")

    role_settings = {role: dict(agents.get(role, {})) for role in ROLES}
    kernel_names = [name for name in agents if name in KERNELS]
    unknown = set(agents) - set(ROLES) - set(KERNELS)
    if unknown:
        raise ValueError("unknown legacy agents: " + ", ".join(sorted(unknown)))
    if len(kernel_names) > 1:
        raise ValueError("legacy agents may select at most one AI kernel")
    if kernel_names:
        kernel = kernel_names[0]
        # Historical kernel profile overrides now configure the NPU role.
        role_settings["npu"].update(agents[kernel])
        return {"type": "ai_kernel", "kernel": kernel}, role_settings
    return {"type": "synthetic"}, role_settings


def apply_manifest(args, manifest):
    if not isinstance(manifest, dict):
        raise ValueError("experiment config must contain a JSON object")
    experiment = manifest.get("experiment", {})
    if not isinstance(experiment, dict):
        raise ValueError("experiment must be a JSON object")
    for source in (manifest, experiment):
        for field in SCALAR_FIELDS:
            if field in source:
                setattr(args, field, source[field])

    memory_options = manifest.get("memory_options", {})
    if not isinstance(memory_options, dict):
        raise ValueError("memory_options must be a JSON object")
    for field in ("simple_latency", "simple_bandwidth"):
        if field in memory_options:
            setattr(args, field, memory_options[field])

    interconnect = manifest.get("interconnect", {})
    if not isinstance(interconnect, dict):
        raise ValueError("interconnect must be a JSON object")
    args.interconnect_options = dict(interconnect)

    if "workload" in manifest:
        workload = manifest["workload"]
        if isinstance(workload, str):
            workload = {"type": workload}
        if not isinstance(workload, dict):
            raise ValueError("workload must be a string or object")
        args.workload_type = workload.get("type", "synthetic")
        args.kernel = workload.get("kernel")
        args.initiator_configs = manifest.get("initiators", {})
    elif "agents" in manifest:
        workload, configs = _legacy_agents(manifest["agents"])
        args.workload_type = workload["type"]
        args.kernel = workload.get("kernel")
        args.initiator_configs = configs
    else:
        args.initiator_configs = manifest.get("initiators", {})

    args.configuration = manifest.get("configuration", args.configuration)
    if not isinstance(args.initiator_configs, dict):
        raise ValueError("initiators must be an object keyed by role")
    args.config_manifest = manifest
    return args


def resolved_manifest(args, profiles):
    result = dict(getattr(args, "config_manifest", {}) or {})
    result.pop("agents", None)
    result["workload"] = {"type": args.workload_type}
    if args.workload_type == "ai_kernel":
        result["workload"]["kernel"] = args.kernel
    result["configuration"] = args.configuration
    result["initiators"] = {
        name: {
            key: value for key, value in profile.__dict__.items()
            if key != "initiator"
        } | {
            "reads_per_block": profile.reads_per_block,
            "writes_per_block": profile.writes_per_block,
            "num_blocks": args.initiator_configs.get(name, {}).get(
                "num_blocks", profile.work_blocks or args.num_blocks),
        }
        for name, profile in profiles.items()
    }
    result["memory"] = args.memory
    result["connection"] = args.connection
    result["mem_size"] = args.mem_size
    result["interconnect"] = dict(getattr(args, "interconnect_options", {}))
    result["experiment"] = {
        "num_blocks": args.num_blocks,
        "cache_line_size": args.cache_line_size,
        "timeout": args.timeout,
    }
    return result
