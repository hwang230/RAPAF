#!/usr/bin/env python3
# Copyright (c) 2026 Hugh Wang
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer; redistributions in binary
# form must reproduce the above copyright notice, this list of conditions and
# the following disclaimer in the documentation and/or other materials
# provided with the distribution; neither the name of the copyright holders
# nor the names of its contributors may be used to endorse or promote products
# derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Run the closed-loop DSP memory prototype against a gem5 memory model."""

import argparse
import json
import os
import sys

import m5
from m5.objects import (
    AddrRange,
    DspInitiator,
    McuInitiator,
    NpuInitiator,
    TrafficRunCoordinator,
    Root,
    SrcClockDomain,
    System,
    VoltageDomain,
)
from m5.util.convert import toMemorySize

from experiment_config import apply_manifest, load_manifest, resolved_manifest
from interconnects import CreditBasedChipletInterconnect, NoncoherentXBarInterconnect
from memory_targets import (
    HBM2Target,
    SimpleMemoryTarget,
    SingleChannelDDR3Target,
    SingleChannelDDR4Target,
    SingleChannelDDR5Target,
)
from metrics import MemoryMetricBundle, write_metric_outputs
from workloads import CONFIGURATIONS, KERNELS, ROLES, WORKLOADS, resolve_profiles


def positive_int(value):
    parsed = int(value, 0)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def nonnegative_int(value):
    parsed = int(value, 0)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="JSON runtime experiment manifest")
    parser.add_argument("--memory", choices=("simple", "ddr3", "ddr4", "ddr5", "hbm2"),
                        default="simple")
    parser.add_argument(
        "--connection",
        choices=("xbar", "chiplet_star", "chiplet_mesh"),
        default="xbar",
    )
    parser.add_argument("--mem-size", default="16MiB")
    parser.add_argument("--simple-latency", default="30ns")
    parser.add_argument("--simple-bandwidth", default="12.8GiB/s")
    parser.add_argument("--sys-clock", default="1GHz")
    parser.add_argument("--request-size", type=positive_int, default=64)
    parser.add_argument("--reads-per-block", type=positive_int, default=8)
    parser.add_argument("--writes-per-block", type=positive_int, default=8)
    parser.add_argument("--request-spacing", default="1ns")
    parser.add_argument("--compute-delay", default="100ns")
    parser.add_argument("--idle-gap", default="1us")
    parser.add_argument("--max-outstanding", type=positive_int, default=1)
    parser.add_argument("--num-blocks", type=positive_int, default=20,
                        help="Blocks processed by each Configuration A initiator")
    parser.add_argument("--input-base", type=nonnegative_int, default=0x1000)
    parser.add_argument("--output-base", type=nonnegative_int, default=0x8000)
    parser.add_argument("--cache-line-size", type=positive_int, default=64)
    parser.add_argument("--timeout", default="10ms",
                        help="Maximum simulated wait for request progress per initiator")
    parser.add_argument("--summary", default="dsp_summary.json")
    parser.add_argument("--metrics-json", default="memory_metrics.json")
    parser.add_argument("--metrics-csv", default="memory_metrics.csv")
    parser.add_argument("--workload", choices=WORKLOADS, default="synthetic")
    parser.add_argument("--kernel", choices=KERNELS)
    parser.add_argument("--configuration", choices=CONFIGURATIONS,
                        default="config_a")
    parser.add_argument("--agents", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.initiator_configs = {}
    args.interconnect_options = {}
    args.config_manifest = {}
    # Standalone command lines use the same synthetic workload default as
    # manifests. Previously this was initialized only while loading a manifest.
    args.workload_type = "synthetic"
    args.kernel = None
    return args


def profiles_for_args(args):
    overrides = {role: dict(args.initiator_configs.get(role, {}))
                 for role in ROLES}
    if args.agents and not args.config:
        from experiment_config import _legacy_agents
        workload, overrides = _legacy_agents(args.agents)
        args.workload_type = workload["type"]
        args.kernel = workload.get("kernel")
    if not args.config and args.workload_type == "synthetic":
        overrides["dsp"].update(
            request_size=args.request_size,
            burst_count=args.reads_per_block,
            read_burst_count=args.reads_per_block,
            write_burst_count=args.writes_per_block,
            request_spacing=args.request_spacing,
            compute_delay=args.compute_delay,
            idle_gap=args.idle_gap,
            max_outstanding=args.max_outstanding,
            input_base=args.input_base,
            output_base=args.output_base,
        )
    for role in ROLES:
        overrides[role].pop("num_blocks", None)
    return resolve_profiles(args.workload_type, args.kernel, overrides)


def validate(args):
    if args.configuration not in CONFIGURATIONS:
        raise ValueError("unsupported hardware configuration")
    if args.workload_type not in WORKLOADS:
        raise ValueError("unknown workload type")
    if args.workload_type == "ai_kernel" and args.kernel not in KERNELS:
        raise ValueError("AI-kernel workload requires a supported kernel")
    if args.workload_type == "synthetic" and args.kernel:
        raise ValueError("kernel can only be set for an AI-kernel workload")
    if args.connection == "direct":
        raise ValueError("Configuration A has multiple initiators; select an interconnect")

    memory_size = toMemorySize(args.mem_size)
    profiles = profiles_for_args(args)
    occupied = []
    for name, profile in profiles.items():
        blocks = int(args.initiator_configs.get(name, {}).get(
            "num_blocks", profile.work_blocks or args.num_blocks))
        if blocks <= 0:
            raise ValueError(f"{name}: num_blocks must be positive")
        if profile.request_size > args.cache_line_size:
            raise ValueError(f"{name}: request size cannot exceed cache-line size")
        if args.cache_line_size % profile.request_size:
            raise ValueError(f"{name}: request size must divide cache-line size")
        if profile.stride < profile.request_size:
            raise ValueError(f"{name}: stride must cover request size")
        streams = {"convolution": 2, "gemm": 2,
                   "attention": 3, "rnn": 3}.get(profile.traffic_style, 1)
        bases = [profile.input_base, profile.aux_base, profile.state_base][:streams]
        if any(base % args.cache_line_size for base in bases + [profile.output_base]):
            raise ValueError(f"{name}: data-region bases must be cache-line aligned")
        if profile.traffic_style in KERNELS:
            configured_reads = [profile.input_reads_per_block,
                                profile.aux_reads_per_block,
                                profile.state_reads_per_block][:streams]
            if any(configured_reads):
                if sum(configured_reads) != profile.reads_per_block:
                    raise ValueError(f"{name}: per-stream read counts must sum to reads_per_block")
                stream_counts = configured_reads
            else:
                stream_counts = [
                    (profile.reads_per_block + streams - 1 - index) // streams
                    for index in range(streams)
                ]
            ends = []
            for base, requests in zip(bases, stream_counts):
                if requests:
                    ends.append(base + (blocks - 1) * profile.region_size
                                + (requests - 1) * profile.stride
                                + profile.request_size)
                else:
                    ends.append(base)
            output_end = (profile.output_base + (blocks - 1) * profile.region_size
                          + (profile.writes_per_block - 1) * profile.stride
                          + profile.request_size)
        else:
            ends = [profile.input_base + blocks * profile.reads_per_block
                    * profile.request_size]
            output_end = (profile.output_base + blocks * profile.writes_per_block
                          * profile.request_size)
        ranges = list(zip(bases, ends)) + [(profile.output_base, output_end)]
        for index, (start, end) in enumerate(ranges):
            if end > memory_size:
                raise ValueError(f"{name}: memory range does not contain workload regions")
            for other_start, other_end in ranges[:index]:
                if start < other_end and other_start < end:
                    raise ValueError(f"{name}: data regions overlap")
            for other_start, other_end, owner in occupied:
                if start < other_end and other_start < end:
                    raise ValueError(f"{name}: data region overlaps {owner}")
            occupied.append((start, end, name))
    return profiles


def build_system(args):
    memory_range = AddrRange(args.mem_size)
    system = System(mem_mode="timing", mem_ranges=[memory_range])
    system.voltage_domain = VoltageDomain()
    system.clk_domain = SrcClockDomain(
        clock=args.sys_clock, voltage_domain=system.voltage_domain
    )

    if args.memory == "simple":
        target = SimpleMemoryTarget(system, memory_range, args.simple_latency,
                                    args.simple_bandwidth)
        memory_settings = (
            f"SimpleMemory latency={args.simple_latency}, "
            f"bandwidth={args.simple_bandwidth}"
        )
    elif args.memory == "ddr3":
        target = SingleChannelDDR3Target(system, memory_range)
        memory_settings = "MemCtrl + DDR3_1600_8x8, channels=1"
    elif args.memory == "ddr4":
        target = SingleChannelDDR4Target(system, memory_range)
        memory_settings = "MemCtrl + DDR4_2400_8x8, channels=1"
    elif args.memory == "ddr5":
        target = SingleChannelDDR5Target(system, memory_range)
        memory_settings = "MemCtrl + DDR5_4400_4x8, channels=1"
    else:
        target = HBM2Target(system, memory_range)
        memory_settings = "HBMCtrl + HBM_2000_4H_1x64, pseudo_channels=2"
    target.attach_monitor()
    initiator_classes = {
        "dsp": DspInitiator,
        "npu": NpuInitiator,
        "mcu": McuInitiator,
    }
    enabled = list(ROLES)
    profiles = validate(args)
    summary_dir = getattr(m5.options, "outdir", os.getcwd())
    coordinator = TrafficRunCoordinator(expected_agents=len(enabled))
    system.coordinator = coordinator
    for name in enabled:
        profile = profiles[name]
        role_options = args.initiator_configs.get(name, {})
        blocks = int(role_options.get(
            "num_blocks", profile.work_blocks or args.num_blocks))
        cls = initiator_classes[name]
        init_kwargs = dict(
            request_size=profile.request_size,
            reads_per_block=profile.reads_per_block,
            writes_per_block=profile.writes_per_block,
            input_reads_per_block=profile.input_reads_per_block,
            aux_reads_per_block=profile.aux_reads_per_block,
            state_reads_per_block=profile.state_reads_per_block,
            request_spacing=profile.request_spacing,
            compute_delay=profile.compute_delay,
            idle_gap=profile.idle_gap,
            max_outstanding=profile.max_outstanding,
            num_blocks=blocks,
            input_base=profile.input_base,
            aux_base=profile.aux_base,
            state_base=profile.state_base,
            output_base=profile.output_base,
            region_size=profile.region_size,
            stride=profile.stride,
            payload_seed=profile.payload_seed,
            cache_line_size=args.cache_line_size,
            timeout=args.timeout,
            summary_path=os.path.abspath(
                (args.summary if os.path.isabs(args.summary) else
                 os.path.join(summary_dir, args.summary)) if name == "dsp" else
                os.path.join(summary_dir, f"{name}_summary.json")
            ),
            backend=args.memory, connection=args.connection,
            memory_settings=memory_settings,
            crossbar_settings="none")
        init_kwargs["traffic_style"] = profile.traffic_style
        init_kwargs.update(
            dimension0=profile.dimension0,
            dimension1=profile.dimension1,
            dimension2=profile.dimension2,
            kernel_size=profile.kernel_size,
            heads=profile.heads,
            tile_size=profile.tile_size,
        )
        if coordinator is not None:
            init_kwargs["coordinator"] = coordinator
        setattr(system, name, cls(**init_kwargs))

    if args.connection == "direct":
        for name in enabled:
            getattr(system, name).port = target.upstream_port
        interconnect = None
    elif args.connection == "xbar":
        options = args.interconnect_options
        interconnect = NoncoherentXBarInterconnect(
            width=int(options.get("width", 16)),
            frontend_latency=int(options.get("frontend_latency", 1)),
            forward_latency=int(options.get("forward_latency", 1)),
            response_latency=int(options.get("response_latency", 1)),
        )
        interconnect.build(system)
        for name in enabled:
            getattr(system, name).port = interconnect.xbar.cpu_side_ports
        interconnect.connect_downstream(target.upstream_port)
    else:
        options = args.interconnect_options
        topology = "mesh_xy" if args.connection == "chiplet_mesh" else "star"
        interconnect = CreditBasedChipletInterconnect(
            topology=topology,
            width=int(options.get("width", 16)),
            link_latency=int(options.get("link_latency", 1)),
            router_latency=int(options.get("router_latency", 1)),
            buffer_depth=int(options.get("buffer_depth", 8)),
        )
        interconnect.build(system)
        for name in enabled:
            getattr(system, name).port = interconnect.cpu_side_ports
        interconnect.connect_downstream(target.upstream_port)
    return system, memory_settings, target, profiles, enabled, interconnect


def main():
    args = arguments()
    try:
        if args.config:
            apply_manifest(args, load_manifest(args.config))
        validate(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        system, memory_settings, target, profiles, enabled, interconnect = build_system(args)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    root = Root(full_system=False, system=system)
    print(
        "DSP prototype: "
        f"backend={args.memory} connection={args.connection} "
        f"memory=({memory_settings})"
    )
    m5.instantiate()
    exit_event = m5.simulate()
    cause = exit_event.getCause()
    code = exit_event.getCode()
    print(f"DSP prototype exit: cause={cause!r}, code={code}")
    if cause not in ("DSP prototype completed", "all agents completed") or code != 0:
        return code if code else 1
    m5.stats.dump()
    bundle = MemoryMetricBundle(target.stable_name)
    stats_path = os.path.join(getattr(m5.options, "outdir", os.getcwd()), "stats.txt")
    metrics = bundle.collect(stats_path)
    result = {"configuration": {"memory": args.memory,
                                 "connection": args.connection,
                                 "configuration": args.configuration,
                                 "workload": {"type": args.workload_type,
                                              **({"kernel": args.kernel}
                                                 if args.kernel else {})},
                                 "initiators": enabled,
                                 "agents": enabled,
                                 "memory_target": target.metadata,
                                 "interconnect": (interconnect.metadata
                                                  if interconnect else
                                                  {"name": "direct"}),
                                 "resolved_manifest": resolved_manifest(
                                     args, profiles)},
              "initiators": {}, "agents": {},
              "memory_systems": {target.stable_name: metrics},
              "aggregate": {}, "verification_passed": True,
              "exit_reason": "all agents completed",
              "phases": ["WARMUP", "MEASURED_TRAFFIC", "DRAIN",
                         "VERIFICATION", "DONE"]}
    for name in enabled:
        output_dir = getattr(m5.options, "outdir", os.getcwd())
        summary_path = ((args.summary if os.path.isabs(args.summary) else
                         os.path.join(output_dir, args.summary)) if name == "dsp" else
                        os.path.join(output_dir, f"{name}_summary.json"))
        if os.path.exists(summary_path):
            with open(summary_path, encoding="utf-8") as stream:
                result["initiators"][name] = json.load(stream)
    result["agents"] = result["initiators"]
    aggregate = {"initiators": enabled, "agents": enabled,
                 "requests": {}, "payload": {},
                 "latency_ticks": {}, "verification_passed": True}
    read_count = write_count = 0
    read_latency = write_latency = 0
    for summary in result["initiators"].values():
        aggregate["verification_passed"] = bool(aggregate["verification_passed"] and
                                                  summary.get("verification_passed", 0))
        for section in ("requests", "payload"):
            for metric, value in summary.get(section, {}).items():
                if isinstance(value, (int, float)):
                    aggregate[section][metric] = aggregate[section].get(metric, 0) + value
        reads = summary.get("requests", {}).get("work_reads_completed", 0)
        writes = summary.get("requests", {}).get("work_writes_completed", 0)
        read_count += reads
        write_count += writes
        read_latency += reads * summary.get("latency_ticks", {}).get("mean_read", 0)
        write_latency += writes * summary.get("latency_ticks", {}).get("mean_write", 0)
    aggregate["latency_ticks"]["mean_read"] = read_latency / read_count if read_count else 0
    aggregate["latency_ticks"]["mean_write"] = write_latency / write_count if write_count else 0
    aggregate["latency_ticks"]["max_read"] = max(
        (s.get("latency_ticks", {}).get("max_read", 0) for s in result["initiators"].values()), default=0)
    aggregate["latency_ticks"]["max_write"] = max(
        (s.get("latency_ticks", {}).get("max_write", 0) for s in result["initiators"].values()), default=0)
    elapsed = max((s.get("measurement", {}).get("elapsed_ticks", 0)
                   for s in result["initiators"].values()), default=0)
    aggregate["measurement_interval"] = {"phase": "MEASURED_TRAFFIC",
                                          "elapsed_ticks": elapsed}
    payload = aggregate["payload"].get("bytes_read", 0) + aggregate["payload"].get("bytes_written", 0)
    aggregate["payload"]["bandwidth_GBps"] = (payload * 1e12 / elapsed / 1e9
                                                if elapsed else 0)
    result["aggregate"] = aggregate
    result["verification_passed"] = aggregate["verification_passed"]
    metrics["interval"]["start_tick"] = 0
    metrics["interval"]["end_tick"] = elapsed
    metrics["interval"]["elapsed_ticks"] = elapsed
    output_dir = getattr(m5.options, "outdir", os.getcwd())
    metrics_json = args.metrics_json if os.path.isabs(args.metrics_json) else os.path.join(output_dir, args.metrics_json)
    metrics_csv = args.metrics_csv if os.path.isabs(args.metrics_csv) else os.path.join(output_dir, args.metrics_csv)
    write_metric_outputs(metrics_json, metrics_csv, result)
    return 0


if __name__ in ("__main__", "__m5_main__"):
    sys.exit(main())
