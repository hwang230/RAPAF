#!/usr/bin/env python3
"""Focused end-to-end checks for the heterogeneous architecture runner.

Pass --gem5 with the binary built from this tree. Each scenario creates an
isolated output directory and checks the initiator's JSON report, rather than
replicating its state-machine implementation.
"""

import argparse
import json
import pathlib
import re
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = ROOT / "src" / "experiments" / "heterogeneous_architecture" / "run_dsp.py"


def run(gem5, root, name, *args, expect_success=True):
    outdir = root / name
    outdir.mkdir()
    summary = outdir / "summary.json"
    command = [
        str(gem5), f"--outdir={outdir}", str(CONFIG), "--summary",
        str(summary), *args,
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    if expect_success and completed.returncode:
        raise AssertionError(
            f"{name} failed ({completed.returncode}):\n{completed.stdout}\n"
            f"{completed.stderr}"
        )
    if not expect_success:
        if completed.returncode == 0:
            raise AssertionError(f"{name} unexpectedly succeeded")
        return completed
    with summary.open() as handle:
        return json.load(handle)


def successful(summary):
    requests = summary["requests"]
    assert summary["verification_passed"], summary
    assert requests["accepted"] == requests["completed"], requests
    assert requests["work_reads_accepted"] == requests["work_reads_completed"]
    assert requests["work_writes_accepted"] == requests["work_writes_completed"]
    assert requests["verify_accepted"] == requests["verify_completed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=pathlib.Path, required=True)
    parser.add_argument("--keep-output", action="store_true")
    args = parser.parse_args()
    if not args.gem5.is_file():
        parser.error(f"not a file: {args.gem5}")

    temporary = tempfile.TemporaryDirectory(prefix="gem5-heterogeneous-architecture-")
    root = pathlib.Path(temporary.name)
    try:
        direct = run(
            args.gem5, root, "config-a", "--connection", "xbar",
            "--num-blocks", "2",
            "--max-outstanding", "1",
        )
        successful(direct)
        assert direct["connection"] == "xbar"
        assert direct["requests"]["peak_outstanding"] == 1

        backpressure = run(
            args.gem5, root, "backpressure", "--connection", "xbar",
            "--num-blocks", "2", "--max-outstanding", "8",
            "--request-spacing", "1ps", "--simple-bandwidth", "1GiB/s",
        )
        successful(backpressure)
        assert backpressure["requests"]["retry_callbacks"] > 0
        assert backpressure["requests"]["peak_outstanding"] <= 8

        ddr = run(
            args.gem5, root, "ddr4", "--memory", "ddr4",
            "--connection", "xbar", "--num-blocks", "3",
            "--max-outstanding", "8", "--request-spacing", "1ps",
        )
        successful(ddr)
        assert ddr["requests"]["peak_outstanding"] > 1

        fast = run(
            args.gem5, root, "fast", "--num-blocks", "2",
            "--simple-latency", "10ns",
        )
        slow = run(
            args.gem5, root, "slow", "--num-blocks", "2",
            "--simple-latency", "100ns",
        )
        successful(fast)
        successful(slow)
        assert (
            slow["measurement"]["mean_block_ticks"]
            > fast["measurement"]["mean_block_ticks"]
        )

        invalid = run(
            args.gem5, root, "invalid", "--request-size", "96",
            expect_success=False,
        )
        assert "request size" in invalid.stderr

        long_gaps = run(
            args.gem5, root, "long-gaps", "--num-blocks", "2",
            "--timeout", "5us", "--compute-delay", "50us", "--idle-gap", "50us",
        )
        successful(long_gaps)

        timeout = run(
            args.gem5, root, "timeout", "--timeout", "50us",
            "--simple-latency", "1ms", expect_success=False,
        )
        assert timeout.returncode == 1
        assert "request-progress timeout" in timeout.stdout
        assert "completed=0" in timeout.stdout
        assert re.search(
            r"outstanding=\d+, waiting for retry=(yes|no)", timeout.stdout
        )

        for kernel in ("convolution", "attention", "gemm", "rnn"):
            outdir = root / kernel
            outdir.mkdir()
            command = [
                str(args.gem5), f"--outdir={outdir}", str(CONFIG),
                "--num-blocks", "2", "--workload", "ai_kernel",
                "--kernel", kernel,
            ]
            completed = subprocess.run(command, text=True, capture_output=True)
            if completed.returncode:
                raise AssertionError(
                    f"{kernel} failed ({completed.returncode}):\n"
                    f"{completed.stdout}\n{completed.stderr}"
                )
            with (outdir / "npu_summary.json").open() as handle:
                npu = json.load(handle)
            successful(npu)
            assert npu["traffic_style"] == kernel
    finally:
        if args.keep_output:
            print(root)
            temporary.cleanup = lambda: None
        else:
            temporary.cleanup()


if __name__ == "__main__":
    main()
