# Codex implementation brief: DSP traffic initiator with gem5 memory targets

## Objective

Implement a small, reproducible gem5 prototype containing one custom DSP-inspired behavioral initiator connected to existing gem5 memory models. Demonstrate correct timing requests/responses, bursty block-processing traffic, backpressure handling, and comparable measurements across memory backends.

This is a traffic/timing model, not a DSP instruction-set simulator or an implementation of FIR/GEMM arithmetic. All numerical defaults below are synthetic test settings, not specifications for commercial DSP hardware.

## Scope

- Required: one initiator; direct connection to SimpleMemory; deterministic data verification; bursty reads/writes; configurable outstanding-request limit; existing single-channel DDR4 backend; metrics and automated smoke tests.
- Add a fixed NoncoherentXBar after the direct SimpleMemory test works. Use the same crossbar for SimpleMemory-versus-DDR4 comparisons.
- Extension after required checks pass: HBM2Stack using all exposed memory ports and their address mapping.
- Defer DSE, NPU/MCU/GPU models, actual inference, local memories, SHUB, custom chiplet interconnects, coherence, power modeling, and a dashboard.

## 1. Inspect and establish the environment

1. Read applicable AGENTS.md files and inspect the repository before editing. Locate the gem5 checkout and identify its version/commit, build configuration, existing project layout, and available binaries.
2. Reuse the installed checkout and its APIs. Use source from that checkout as the authority when tutorial signatures differ. Do not silently upgrade gem5 or overwrite unrelated changes.
3. If gem5 is absent, report that fact and set up a pinned checkout/build if the execution environment permits. Record the version and exact commands. If dependencies or network restrictions block building, clearly distinguish source preparation from executed validation.
4. Run a minimal existing example to establish that the gem5 binary works before debugging custom code. Use an appropriate existing build; this traffic-only prototype does not require an executing CPU or a Linux guest.

## 2. Implement a custom initiator SimObject

Suggested files; adapt paths to repository conventions:

| File | Responsibility |
| --- | --- |
| src/configuration/configurationA/DspInitiator.py | DSP SimObject declaration and concrete requestor identity |
| src/configuration/configurationA/synthetic_initiator_base.hh | Shared C++ transport, phase, retry, and outstanding-request interface |
| src/configuration/configurationA/synthetic_initiator_base.cc | Shared traffic engine, responses, retries, events, and statistics |
| src/configuration/configurationA/SConscript | Register base and concrete SimObjects and debug flags |
| src/experiments/heterogeneous_architecture/run_dsp.py | System setup, CLI, backend selection, simulation exit |
| tests/heterogeneous_architecture/ | Small executable acceptance checks |
| docs/heterogeneous_architecture.md | Build/run instructions, semantics, limitations |

Use gem5 events to schedule activity. Implement the request-side port callbacks required by the chosen version, including recvTimingResp, recvReqRetry, and range-change handling. Set up requestor identity, address ranges, timing memory mode, clock/voltage domains, System, and Root as required by the checkout.

Memory traffic must use gem5 timing requests. Do not substitute atomic or functional accesses for the measured workload. Functional initialization is acceptable before measurement if supported and explicitly documented; timing initialization is also acceptable.

### Request ownership and backpressure

- A successful sendTimingReq transfers the request into the downstream timing path. Increment accepted/outstanding counters only on acceptance.
- On a failed send, retain that exact packet and its data. Do not advance its address/index, count it as accepted, or create a duplicate.
- Stop sending requests on the blocked port until recvReqRetry arrives. The callback permits another attempt; it does not guarantee acceptance.
- Keep generation, attempts, accepted transactions, responses, and retries distinct.
- Track each accepted transaction until its response. Support response reordering; do not assume a FIFO response order or a particular memory-controller schedule.
- Keep response acceptance simple: consume responses immediately and return true initially, without a separate finite response queue.
- Use correct packet/data ownership and release completed packets safely according to the checkout's API.
- Ensure retries and scheduled send events cannot issue the same packet twice or recursively corrupt state.

## 3. Milestone A: direct SimpleMemory correctness test

Connection: custom initiator RequestPort directly to SimpleMemory's response port.

Start with one outstanding request. Write deterministic data into a small valid address range, await completion, read it back, and check every byte. Finish with zero outstanding transactions and no retained blocked packet.

Pass criteria:

- Readback matches all writes.
- Accepted request count equals completed response count.
- No response is unmatched, counted twice, or treated as a new request.
- The run terminates successfully only after completion; a watchdog/timeout produces a failure.

Do not proceed to detailed-memory debugging until this checkpoint passes.

## 4. Milestone B: bursty DSP block-processing profile

Implement an explicit state sequence:

INITIALIZE -> READ_BURST -> WAIT_READS -> COMPUTE -> WRITE_BURST -> WAIT_WRITES -> IDLE -> next block -> DONE

- READ_BURST issues sequential reads from an initialized input region, subject to minimum request spacing and the outstanding limit.
- WAIT_READS prevents computation from starting until every required read response has arrived.
- COMPUTE schedules a configured simulated delay. It does not execute DSP mathematics.
- WRITE_BURST issues sequential output writes with a deterministic block/address-dependent payload.
- WAIT_WRITES waits for every required write response before advancing.
- IDLE is a gap after block completion, not a fixed absolute release period. Slow memory therefore delays subsequent blocks. Document this closed-loop behavior.
- After the last block, verify output storage through a separate readback phase and exit when all verification responses complete.

Expose at least these CLI parameters:

| Parameter | Suggested default | Semantics |
| --- | --- | --- |
| request size | 64 B | Each packet's data size |
| reads per block | 8 | 512 B input payload per block |
| writes per block | 8 | 512 B output payload per block |
| request spacing | 1 ns | Minimum spacing between accepted sends, including retry acceptance |
| compute delay | 100 ns | Begins after the final input response |
| idle gap | 1 us | Begins after the final output-write response |
| max outstanding | 1 initially | Validate at 1, then enable a burst demonstration at 8 |
| number of blocks | 20 | Finite run |
| memory selection | simple | simple or ddr4; hbm2 only when implemented |
| connection | direct | direct initially; xbar for backend comparisons |

Input and output regions must be disjoint, aligned, deterministic, and inside the memory range. Use a small common working set; do not require all backends to accept arbitrary equal capacities. Constrain packet sizes so they do not cross the configured cache-line boundaries; reject invalid settings clearly. Validate positive counts/spacing, nonnegative delays, sufficient memory, and maximum outstanding >= 1.

Here, a burst means a sequence of closely spaced gem5 packets, not an AXI burst protocol. With max outstanding = 1, requests are serialized by responses; this is a correctness mode rather than evidence of high-concurrency bursting.

## 5. Milestone C: interchangeable existing memory backends

1. Add an existing NoncoherentXBar between the initiator and memory. Validate SimpleMemory through it first.
2. Add the existing single-channel DDR4-2400 configuration, or the checkout's equivalent MemCtrl plus DDR4 interface. Reuse device timing and controller behavior.
3. Keep backend selection inside Python configuration. The C++ initiator must remain unchanged when selecting memory.
4. Keep request stream settings, working-set addresses, crossbar settings, and measurement boundaries identical for backend comparisons. Record actual memory capacity and channel count.
5. If standard-library memory components are used, follow their initialization/range setup contract, attach them into the System hierarchy, and connect every exposed address-range/port pair. Do not assume every memory system exposes one .port attribute.
6. Optional HBM2: use HBM2Stack/HBMCtrl support supplied by the checkout. Preserve channel/pseudo-channel address mapping, connect all ports through the crossbar, and validate that addresses reach the intended controllers. HBM2 may have greater aggregate resources than DDR4; present this as a system comparison rather than an equal-resource technology comparison.

Do not claim a backend is supported until its actual gem5 simulation passes. Do not implement new memory controllers. If HBM2 support is unavailable or integration fails, complete SimpleMemory and DDR4 and report the exact extension blocker.

## 6. Measurements and output

Produce normal gem5 statistics plus a concise machine-readable summary (JSON or CSV):

- gem5 version/commit, backend, connection, all workload parameters, memory/crossbar settings;
- measured accepted/completed reads and writes; initialization and verification counts separately;
- payload bytes read/written;
- mean/max accepted-request-to-response latency, ideally separated for reads and writes;
- rejected-send attempts, retry callbacks, and peak outstanding count;
- per-block completion time, mean block time, and total measured elapsed time;
- achieved payload bandwidth = measured payload bytes / measured elapsed time, with explicit units;
- verification status and simulation exit reason.

Define the measured interval as first accepted workload read through final workload write response. It includes intervening compute and idle gaps but excludes initialization and final verification. Also report each block from its first accepted read through its final write response. Do not present whole-run payload bandwidth as peak memory bandwidth.

Optional debug tracing should identify simulated time, phase/block, address, operation, and request ID. Normal runs should not print every transaction. A request rejected several times must still contribute one accepted transaction and one eventual response.

## 7. Acceptance checks

Implement and run focused integration checks, not tests that merely duplicate implementation logic:

1. Direct SimpleMemory: deterministic write/readback with one outstanding transaction.
2. DSP phases: correct data, exact workload request totals, compute starts after all reads, next block starts after required writes and idle gap.
3. Concurrency: max outstanding = 8; observed count never exceeds the limit. Demonstrate more than one outstanding request under suitable settings.
4. Backpressure: deliberately reduce SimpleMemory bandwidth and issue requests fast enough to cause rejection/retry. Require retries > 0, correct data, exact completion counts, and no hang.
5. Timing sensitivity: with max outstanding = 1, increase SimpleMemory latency and verify block completion time increases. Do not require an exact timing formula without accounting for all modeled delays.
6. Backend smoke runs: identical workload settings through the same crossbar against SimpleMemory and DDR4; require successful completion and verification for both. Include HBM2 only if implemented.
7. Invalid parameters and timeout: clear nonzero failure, never a false success.

Performance comparisons need not show DDR4/HBM always winning; a small or serialized workload may not utilize their bandwidth. Assess correctness before interpreting speed differences.

## 8. Deliverables and implementation order

Implement and verify milestones A, B, then C. After each milestone, keep the changes runnable. Deliver source, configuration, acceptance checks, README, exact build/run commands for this checkout, and a small example results table from real runs.

Conclude with what was implemented, commands actually executed, test outcomes, and remaining limitations. If execution was blocked, explicitly say which checks were not run. Do not report hypothetical results as measurements.

## Official references

- Custom SimObjects: https://www.gem5.org/documentation/learning_gem5/part2/helloobject/
- Events: https://www.gem5.org/documentation/learning_gem5/part2/events/
- Timing packets, ports, and retries: https://www.gem5.org/documentation/learning_gem5/part2/memoryobject/
- Memory-system interface: https://www.gem5.org/documentation/general_docs/stdlib_api/gem5.components.memory.abstract_memory_system.html
- Existing DDR configurations: https://www.gem5.org/documentation/general_docs/stdlib_api/gem5.components.memory.single_channel.html
- HBM2 components: https://www.gem5.org/documentation/general_docs/stdlib_api/gem5.components.memory.hbm.html

Treat these as conceptual references. Inspect the selected checkout's source for exact APIs.
