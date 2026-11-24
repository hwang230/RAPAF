# gem5 / GARNET setup

This project uses the official `gem5` `stable` branch, checked out in
`gem5/` at commit `f5c5a6e390f55dd5984977815bf9d0bd05da6945` (2026-09-07).

The proposal targets GARNET, so use the small, focused build rather than the
all-ISA build:

```sh
cd /path/to/gem5
PYTHON_CONFIG="$(command -v python3-config)" \
  scons build/Garnet_standalone/gem5.opt -j2
```

This produces `build/Garnet_standalone/gem5.opt`, which includes the
`Garnet_standalone` Ruby protocol and the synthetic traffic entry point at
`configs/example/garnet_synth_traffic.py`.

## Host prerequisites

* C/C++ compiler: Apple Clang 17 is present.
* Python development headers plus `python3-config`: install a Homebrew Python
  (3.12+). Point `PYTHON_CONFIG` to its versioned config utility if it is not
  on `PATH`.
* SCons 3+: install it directly or run it from a virtual environment.
* `zlib` and `m4`: required by gem5. They are supplied by macOS/Homebrew on
  this host; the build will verify them.
* Protobuf: optional, only needed for trace capture/playback.
* Boost: optional, only needed for SystemC; Homebrew Boost is already present.

Homebrew on this Intel macOS host builds packages from source. Once its active
package transaction has completed, verify the configuration tool with:

```sh
"$(command -v python3-config)" --embed --ldflags --includes
```

Then rerun the focused build command above. The first build is intentionally
limited to two jobs to avoid memory pressure.
