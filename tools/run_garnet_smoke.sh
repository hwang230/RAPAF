#!/bin/sh

set -eu

task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
task_gem5=${GEM5_ROOT:-"$task_root/../gem5"}
task_binary="$task_gem5/build/Garnet_standalone/gem5.opt"
task_output=${GARNET_OUTPUT_DIR:-"$task_root/runs/smoke-2x2"}
task_log=${GARNET_LOG_FILE:-"$task_root/runs/smoke-2x2.log"}

mkdir -p "$(dirname -- "$task_output")" "$(dirname -- "$task_log")"

for task_wait in $(seq 1 "${GARNET_WAIT_CYCLES:-120}"); do
    if [ -x "$task_binary" ]; then
        exec "$task_binary" --outdir="$task_output" \
            "$task_gem5/configs/example/garnet_synth_traffic.py" \
            --network=garnet --topology=Mesh_XY --mesh-rows=2 \
            --num-cpus=4 --num-dirs=4 --synthetic=uniform_random \
            --injectionrate=0.01 --sim-cycles=1000 --routing-algorithm=1 \
            >"$task_log" 2>&1
    fi
    sleep "${GARNET_WAIT_SECONDS:-30}"
done

echo "Timed out waiting for gem5 to build." >"$task_log"
exit 1
