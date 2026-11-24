# RISC-V Architecture Performance Project

This repository contains a custom gem5 memory-traffic extension for the
capstone project. It intentionally does not vendor the full gem5 source tree.

The extension separates workload definitions from hardware initiators.
Configuration A runs DSP, NPU, and MCU timing traffic together; AI-kernel
workloads are issued by the NPU while DSP and MCU model supporting transfer,
signal, and control traffic. Interconnects and gem5 memory targets are selected
independently.

## Repository layout

```text
src/configuration/configurationA/                  DSP/NPU/MCU gem5 SimObjects
src/experiments/heterogeneous_architecture/        Run configuration and workload models
src/experiments/experiments.py                     Sweep and report driver
tests/heterogeneous_architecture/                  End-to-end acceptance tests
docs/                                             Project behavior and research notes
experiments/                                      Local results and experiment manifests
patches/                                          Optional project-specific gem5 patches
```

## Use with gem5

The extension targets gem5 revision `f5c5a6e390f55dd5984977815bf9d0bd05da6945`.
Clone that revision separately, then copy the extension directories into the
gem5 checkout while preserving their paths:

```sh
rsync -a src/experiments/heterogeneous_architecture/ /path/to/gem5/configs/heterogeneous_architecture/
rsync -a src/configuration/configurationA/ \
  /path/to/gem5/src/configuration/configurationA/
rsync -a tests/heterogeneous_architecture/ /path/to/gem5/tests/heterogeneous_architecture/
```

RAPAF is the source of truth for these project files. The `rsync` commands place
temporary build copies in gem5's expected integration directories; keep those
copies and generated run files out of the gem5 repository history.

Build the focused gem5 binary from the gem5 checkout:

```sh
PYTHON_CONFIG=/path/to/gem5-python-config \
  scons build/Garnet_standalone/gem5.opt -j2
```

Run Configuration A against SimpleMemory:

```sh
build/Garnet_standalone/gem5.opt \
  --outdir=out/dsp-direct \
  configs/heterogeneous_architecture/run_dsp.py \
  --memory=simple --connection=xbar --max-outstanding=1
```

Run an AI-kernel workload through Configuration A:

```sh
build/Garnet_standalone/gem5.opt \
  --outdir=out/attention-ddr4 configs/heterogeneous_architecture/run_dsp.py \
  --workload=ai_kernel --kernel=attention \
  --configuration=config_a --connection=xbar --memory=ddr4
```

Run the acceptance suite:

```sh
python3 tests/heterogeneous_architecture/run_acceptance.py \
  --gem5 build/Garnet_standalone/gem5.opt
```

Generated builds, logs, statistics, caches, and simulation outputs are
excluded by `.gitignore`.
