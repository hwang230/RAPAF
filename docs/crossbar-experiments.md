# Crossbar regression and parameter sweeps

The experiment helper in `src/experiments/experiments.py` wraps the existing gem5
`run_dsp.py` configuration. It uses native DSP/NPU/MCU summaries, the existing
timing crossbar and memory models, and the existing workload measurement
window. It does not implement or simulate chiplets.

## Captured references

The current reference suite is a two-block mixed DSP/NPU/MCU workload, with
the same initiator profiles, 64 byte cache line, 16 byte crossbar, 1 GHz clock,
and seed values in the resolved case manifests. It includes SimpleMemory at
30 ns and one DDR4-2400 channel. Each case was run twice before the runner or
CLI changes. All three initiators passed native data verification, and
requests accepted/completed counts, timing, payload and retry metrics matched
exactly across repetitions. Raw `config.ini`, `config.json`, `stats.txt`, logs,
per-initiator JSON, and provenance are kept under
`experiments/references/crossbar-v1/`; compact expected metrics are in
`experiments/references/expected/`.

Historical per-initiator metrics compare exactly for integer counters/ticks
and with version 1 tolerance for floating point values (`rtol=1e-6`, `atol=
1e-6 ns` for latency and `1e-6 GB/s` for throughput). Revisions are recorded;
the gem5 revision and binary hash must match. A check never updates references.
Use `record-reference` only to create a separately reviewed new reference.

## Commands

From `RAPAF/`:

```sh
# Rerun every required baseline and compare with preserved pre-change metrics.
python3 src/experiments/experiments.py regression \
  --suite experiments/cases/reference-suite.json \
  --output-root experiments/runs/reference-check

# Or check one case in isolation.
python3 src/experiments/experiments.py check \
  --manifest experiments/cases/mixed-simple.json \
  --reference experiments/references/expected/simple-mixed.json \
  --output experiments/runs/simple-check

# Expand and validate one-factor sweeps without starting gem5.
python3 src/experiments/experiments.py sweep \
  --manifest experiments/cases/mixed-simple.json \
  --parameter interconnect.width --values '[8,16,32,64]' \
  --output experiments/sweeps/width-delivery --dry-run

python3 src/experiments/experiments.py sweep \
  --manifest experiments/cases/mixed-simple.json \
  --parameter initiators.npu.max_outstanding --values '[1,4,8,16,64]' \
  --output experiments/sweeps/npu-outstanding-delivery --dry-run

# Execute the bounded sweep; existing output directories are never overwritten.
python3 src/experiments/experiments.py sweep \
  --manifest experiments/cases/mixed-simple.json \
  --parameter interconnect.width --values '[8,16,32,64]' \
  --output experiments/sweeps/width-delivery

python3 src/experiments/experiments.py sweep \
  --manifest experiments/cases/mixed-simple.json \
  --parameter initiators.npu.max_outstanding --values '[1,4,8,16,64]' \
  --output experiments/sweeps/npu-outstanding-delivery
```

Every execution writes its input manifest, command, provenance, native raw
files, and `results.json`. Sweep-level `sweep.csv` and `sweep.json` retain all
points and statuses; `throughput.svg`/`mean_latency.svg` and their PNG/PDF
exports plot successful points only. PNG/PDF export uses matplotlib. Novel
points have `regression_status=not_baselined`. The nominal
width and NPU outstanding settings are included in both sweeps. A fixed-work
configuration is used, so the request counts remain invariant.

For an explicitly reviewed reference recording, use
`src/experiments/experiments.py record-reference --manifest CASE.json --output
OUTPUT_DIR`; this reruns the simulation and writes a separate `*-reference.json`
beside the output directory. Failed simulations return nonzero and cannot be
recorded as successful references. To aggregate normalized run files, pass a
JSON list of their paths to:

```sh
python3 src/experiments/experiments.py report \
  --results '["run-a/results.json","run-b/results.json"]' \
  --output REPORT_DIR
```

Resume is conservative. Run
`src/experiments/experiments.py resume --manifest CASE.json --output-root RUNS_DIR`.
It reuses a successful result only when configuration ID, seed, source-code
identity, gem5 revision, and gem5 binary hash all match. Otherwise it creates a
fresh unique execution. Regression checking across revisions remains an
explicit `check` or `regression` command; both compare configuration IDs,
seed, build identity, native verification, counts, timing, payload, latency,
and supported finite memory-monitor metrics.

## Interpretation

The formula-driven convolution, GEMM, and attention comparisons use matched
61,440-byte input operand footprints and compare SimpleMemory with DDR4 while
DSP, NPU, and MCU share the same crossbar. Their per-kernel formulas,
dimensions, and assumptions are defined in
[workloads.py](../src/experiments/heterogeneous_architecture/workloads.py); raw results
are under `experiments/ai-kernel-formula-runs-2026-11-21-final/`.

The NPU is a sustained stream generator and is the high-demand initiator in
this existing mixed workload. The short finite experiment does not by itself
prove crossbar saturation; a flat curve can also reflect memory service,
generator concurrency, or the small amount of traffic. Throughput is completed
read plus write payload bytes divided by the existing workload window, which
starts at the first accepted workload read and ends at the final workload
write response. Initialization and final verification are excluded. This is
achieved throughput for this synthetic configuration, not a peak-memory claim.

The executed SimpleMemory sweeps recorded:

| Sweep variable | Points | Completed payload throughput (GB/s) | Result |
| --- | --- | --- | --- |
| Crossbar width 8, 16, 32, 64 B/cycle | 4/4 passed | 2.410, 2.440, 2.442, 2.438 | Widths above 16 B/cycle changed throughput very little in this run. |
| NPU outstanding limit 1, 4, 8, 16, 64 | 5/5 passed | 2.437, 2.427, 2.440, 2.440, 2.440 | NPU mean response latency stayed at 35,000 ticks across these points. |

These observations indicate this workload did not show a clear outstanding
limit bottleneck above 1 or a strong width sensitivity above 16 B/cycle. They
do not identify the limiting component. Every sweep point remains
`not_baselined`; values across intentionally changed configurations are not
regression comparisons.

No request-spacing or memory-latency sweep is included in the initial sweep.
The existing controls can expose these later, after the width and outstanding
paths are established. The result contract identifies the architecture as
`crossbar`; selecting `architecture: "chiplet"` reports
`unsupported/not implemented`. The existing crossbar construction remains
behind its interconnect class. Crossbar width is bytes per cycle; the
NPU outstanding sweep changes only that initiator. All other manifest values
remain fixed. The gem5 tick frequency is taken from the run log, not assumed.

The direct standalone CLI now defaults to the synthetic workload, and default
DSP summaries are placed in the run output directory. Manifest-based commands
remain supported.
