# DSP memory prototype

`DspInitiator`, `NpuInitiator`, and `McuInitiator` are the fixed hardware roles
in Configuration A. Workload selection is a separate layer: synthetic traffic
or an AI kernel (convolution, attention, GEMM, or RNN). The NPU issues the
selected kernel profile; DSP models supporting data movement/signals and MCU
models control traffic. These are timing-only behavioral initiators, not
processor or arithmetic models. They write
deterministic data and perform finite
closed-loop blocks of sequential input reads, simulated compute, sequential
output writes, an idle gap, and final timing readback verification.

The state sequence is:

`INITIALIZE -> READ_BURST -> WAIT_READS -> COMPUTE -> WRITE_BURST ->
WAIT_WRITES -> IDLE -> ... -> VERIFY -> DONE`

The idle gap starts only after the final output-write response. Consequently,
slower memory also shifts later blocks; this is not a fixed absolute release
schedule. A burst means closely spaced gem5 timing packets, not an AXI burst.

The C++ transport/correctness implementation is
`src/configuration/configurationA/SyntheticInitiatorBase`, while the
three concrete SimObjects are peer classes that each have their own C++ type
and requestor identity. Reusable profiles live in
`src/experiments/heterogeneous_architecture/workloads.py`. Kernel identity selects the NPU traffic
profile without creating a kernel-specific hardware initiator. Role traffic
remains independent; explicit handoff events are not modeled.

`TrafficRunCoordinator` waits for all three Configuration A initiators and
ends the run on the first failure. Each initiator has a request-progress
watchdog armed while it has an outstanding request or is waiting to retry a
rejected send. A response refreshes the initiator's deadline. When no request
is waiting, scheduled compute and idle gaps can run without a watchdog event.
`--timeout` (also `timeout` in a manifest) is the allowed simulated gap in
request progress for one initiator. Each initiator emits its own summary and
the runner emits aggregate counters.

`NoncoherentXBarInterconnect` and the memory target classes are independent
from workload and initiator logic. Targets insert timing `CommMonitor`
instances with stable names `sram` (SimpleMemory) and `ddr` (DDR4).

## Build

Build from the gem5 checkout after applying this repository's extension. If
`python3-config` is not on `PATH`, set `PYTHON_CONFIG` to the appropriate
versioned configuration utility:

```sh
PYTHON_CONFIG="$(command -v python3-config)" \
scons -j2 build/Garnet_standalone/gem5.opt
```

## Runs

Configuration A runs all three initiators through a shared interconnect:

```sh
build/Garnet_standalone/gem5.opt \
  --outdir=out/config-a-simple configs/heterogeneous_architecture/run_dsp.py \
  --workload=synthetic --configuration=config_a \
  --memory=simple --connection=xbar --max-outstanding=1
```

Backend comparison: preserve the same workload and use the same
`NoncoherentXBar` in both runs.

```sh
build/Garnet_standalone/gem5.opt \
  --outdir=out/dsp-simple-xbar configs/heterogeneous_architecture/run_dsp.py \
  --memory=simple --connection=xbar --max-outstanding=8

build/Garnet_standalone/gem5.opt \
  --outdir=out/dsp-ddr4-xbar configs/heterogeneous_architecture/run_dsp.py \
  --memory=ddr4 --connection=xbar --max-outstanding=8
```

Select `--workload=synthetic` or `--workload=ai_kernel --kernel=convolution|attention|gemm|rnn`.
The only implemented hardware configuration is `--configuration=config_a`.
`--request-size`, `--reads-per-block`, `--writes-per-block`,
`--request-spacing`, `--compute-delay`, `--idle-gap`, `--max-outstanding`,
`--num-blocks`, `--memory`, and `--connection` are exposed by the runner.
Request size must be a positive divisor of the cache-line size; ranges must be
disjoint, aligned, and fit in `--mem-size`.

The JSON manifest keeps the layers independent:

```json
{
  "workload": {"type": "ai_kernel", "kernel": "attention"},
  "configuration": "config_a",
  "initiators": {"dsp": {}, "npu": {}, "mcu": {}},
  "connection": "xbar",
  "memory": "ddr4"
}
```

`workload.type` may be `synthetic` or `ai_kernel`; AI-kernel workloads accept
`convolution`, `attention`, `gemm`, and `rnn`. Legacy manifests using
`agents` are normalized to this structure. `direct` wiring is unavailable for
Configuration A because its three initiators share the selected interconnect.

Configuration A always runs DSP, NPU, and MCU. `--num-blocks` sets the shared
experiment scale; per-role settings can override it in a manifest. Workload,
configuration, interconnect, and memory are independent manifest fields. Old
`agents` manifests are accepted and normalized to the new workload and role
fields.

Synthetic NPU traffic uses sustained input/weight and output streams. AI-kernel
profiles use tiled stream patterns selected on the NPU. DSP uses finite
read/compute/write bursts and MCU uses serialized control/descriptor accesses
with periodic gaps.

The normal gem5 statistics contain `system.dsp.*` counters. The runner also
writes JSON (default `dsp_summary.json`) with accepted/completed requests,
retries, outstanding peak, latency values, per-block times, measured payload
bandwidth, settings, verification status, and exit reason. The measured window
starts at the first accepted workload read and ends at the final workload write
response. It includes compute and idle time, but excludes initialization and
verification, so it is achieved workload bandwidth rather than a peak-memory
claim.

The runner also writes `memory_metrics.json` and `memory_metrics.csv`. Each
memory-system bundle records common CommMonitor statistics, supported native
controller/DRAM/power statistics, units, native names, semantics, interval
fields, and per-agent/aggregate results. Histogram percentiles are marked as
estimates. Unsupported metrics are explicit `null` values with
`supported=false` and a reason, never zero.

To compare histogram bins from several configurations collected in one
experiment, call `write_configuration_histogram` (or its `write_histogram`
alias) from `src/experiments/heterogeneous_architecture/metrics.py`:

```python
write_configuration_histogram(
    "latency-comparison.csv",
    {"simple-xbar": simple_result, "ddr-xbar": ddr_result},
    metric="read_latency_histogram",
)
```

The output is long-form CSV, or JSON when the filename ends in `.json`, with
one row per configuration/bin and units, native statistic names, semantics,
estimate flags, and unsupported reasons preserved. To also create a portable,
dependency-free grouped-bar chart, call:

```python
write_configuration_histogram_svg(
    "latency-comparison.svg",
    {"simple-xbar": simple_result, "ddr-xbar": ddr_result},
    metric="read_latency_histogram",
)
```

The latest all-agent comparison is under
`out/architecture_exploration/heterogeneous_architecture/current_run/`. It contains SimpleMemory
and DDR4 runs using the same DSP+NPU+MCU initiator set, plus comparative
`read_latency_histogram.csv`, `read_latency_histogram.svg`, and
`outstanding_reads.csv` files.

## Example results

These real runs used gem5 `f5c5a6e`, two blocks, 64-byte requests, eight reads
and writes per block, `max_outstanding=8`, `request_spacing=1ps`, and the
fixed 16-byte `NoncoherentXBar` configuration above. They are a small-system
sanity check, not a peak-bandwidth comparison.

| Backend | Measured ticks | Payload bandwidth (B/s) | Retries | Peak outstanding |
| --- | ---: | ---: | ---: | ---: |
| SimpleMemory | 1,480,000 | 1,383,783,783.784 | 116 | 8 |
| DDR4-2400 (one channel) | 1,400,000 | 1,462,857,142.857 | 50 | 8 |

## Acceptance suite

```sh
python3 tests/heterogeneous_architecture/run_acceptance.py \
  --gem5 build/Garnet_standalone/gem5.opt
```

The suite checks Configuration A data correctness, phase totals, backpressure/retry,
outstanding-limit behavior, DDR4-through-crossbar completion, latency
sensitivity, invalid inputs, and the watchdog. HBM2 is deliberately deferred:
this prototype only claims the configurations actually exercised above.

## Limits

There is no cache/coherence hierarchy, local DSP memory, dashboard, or DSE
loop. HBM2 and credit-based chiplet interconnect remain future extensions.
Native monitor/controller statistics are collected from the completed gem5
run; the per-agent summary defines the precise measured workload interval,
while initialization and verification remain separately counted. The packet
stream uses normal gem5 timing requests and intentionally consumes responses
immediately; it supports response reordering by tracking every accepted packet
identity independently.
