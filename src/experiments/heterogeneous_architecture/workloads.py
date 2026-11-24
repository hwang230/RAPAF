"""Workload definitions and role-specific traffic profiles.

Workload identity is independent from the hardware initiator that drives it.
Configuration A runs DSP, NPU, and MCU traffic together. AI kernels select an
NPU traffic profile while DSP and MCU retain their transfer/signal and control
traffic profiles.
"""

from dataclasses import dataclass


ROLES = ("dsp", "npu", "mcu")
KERNELS = ("convolution", "attention", "gemm", "rnn")
WORKLOADS = ("synthetic", "ai_kernel")
CONFIGURATIONS = ("config_a",)


@dataclass(frozen=True)
class WorkloadProfile:
    initiator: str
    read_streams: tuple
    write_streams: tuple
    input_base: int
    aux_base: int
    state_base: int
    output_base: int
    region_size: int
    request_size: int
    stride: int
    burst_count: int
    request_spacing: str
    max_outstanding: int
    compute_delay: str
    idle_gap: str
    payload_seed: int
    traffic_style: str
    dimension0: int
    dimension1: int
    dimension2: int
    kernel_size: int
    heads: int
    tile_size: int
    input_reads_per_block: int = 0
    aux_reads_per_block: int = 0
    state_reads_per_block: int = 0
    work_blocks: int = 0
    output_channels: int = 0
    traffic_formula: str = ""
    traffic_model: dict = None
    verification: str = "final"
    read_burst_count: int = 0
    write_burst_count: int = 0

    @property
    def reads_per_block(self):
        stream_reads = (self.input_reads_per_block + self.aux_reads_per_block
                        + self.state_reads_per_block)
        if stream_reads:
            return stream_reads
        return self.read_burst_count or self.burst_count

    @property
    def writes_per_block(self):
        return self.write_burst_count or self.burst_count


def _profile(role, defaults, overrides):
    values = dict(defaults)
    values.update(overrides)
    return WorkloadProfile(initiator=role, **values)


ROLE_DEFAULTS = {
    "dsp": dict(read_streams=("sequential_input",),
                write_streams=("sequential_output",), input_base=0x1000,
                aux_base=0x100000, state_base=0x200000,
                output_base=0x8000, region_size=0x1000, request_size=64,
                stride=64, burst_count=8, request_spacing="1ns",
                max_outstanding=1, compute_delay="100ns", idle_gap="1us",
                payload_seed=0xD5, traffic_style="dsp_block",
                dimension0=128, dimension1=128, dimension2=64,
                kernel_size=3, heads=1, tile_size=16),
    "npu": dict(read_streams=("input_tile", "weight_tile"),
                write_streams=("output_tile",), input_base=0x40000,
                aux_base=0x50000, state_base=0x60000,
                output_base=0x60000, region_size=0x4000, request_size=64,
                stride=64, burst_count=16, request_spacing="2ns",
                max_outstanding=8, compute_delay="0ns", idle_gap="0ns",
                payload_seed=0x4E, traffic_style="npu_stream",
                dimension0=128, dimension1=128, dimension2=64,
                kernel_size=3, heads=1, tile_size=16),
    "mcu": dict(read_streams=("control",),
                write_streams=("descriptor",), input_base=0x70000,
                aux_base=0x71000, state_base=0x72000,
                output_base=0x78000, region_size=0x1000, request_size=32,
                stride=32, burst_count=1, request_spacing="250ns",
                max_outstanding=1, compute_delay="0ns", idle_gap="2us",
                payload_seed=0x4D, traffic_style="mcu_control",
                dimension0=128, dimension1=128, dimension2=64,
                kernel_size=1, heads=1, tile_size=16),
}


ELEMENT_BYTES = 2  # FP16 traffic model
REQUEST_BYTES = 64
NPU_PEAK_FLOPS = 1_000_000_000_000  # common 1-TFLOP/s timing assumption


def _ceil_div(numerator, denominator):
    return (numerator + denominator - 1) // denominator


def _requests_for(elements):
    return _ceil_div(elements * ELEMENT_BYTES, REQUEST_BYTES)


def _compute_delay_ns(flops):
    return max(1, _ceil_div(flops * 1_000_000_000, NPU_PEAK_FLOPS))


def _convolution_defaults():
    height, width = 31, 32
    channels_in, channels_out = 24, 32
    kernel = 3
    tile = 16
    output_height = height  # SAME padding; edge tiles use the full padded tile
    output_width = width
    input_elements = (tile + kernel - 1) ** 2 * channels_in
    weight_elements = kernel ** 2 * channels_in * tile
    output_elements = tile ** 2 * tile
    input_reads = _requests_for(input_elements)
    weight_reads = _requests_for(weight_elements)
    writes = _requests_for(output_elements)
    output_tiles = (_ceil_div(output_height, tile) *
                    _ceil_div(output_width, tile) *
                    _ceil_div(channels_out, tile))
    flops = 2 * tile * tile * tile * kernel * kernel * channels_in
    return dict(
        read_streams=("input_patch", "weight_tile"),
        write_streams=("output_tile",), input_base=0x100000,
        aux_base=0x200000, state_base=0x300000, output_base=0x400000,
        region_size=0x10000, request_size=REQUEST_BYTES, stride=REQUEST_BYTES,
        burst_count=input_reads + weight_reads, read_burst_count=input_reads + weight_reads,
        write_burst_count=writes, request_spacing="1ns", max_outstanding=8,
        compute_delay=f"{_compute_delay_ns(flops)}ns", idle_gap="0ns",
        payload_seed=0xC0, traffic_style="convolution",
        dimension0=height, dimension1=width, dimension2=channels_in,
        kernel_size=kernel, heads=1, tile_size=tile,
        input_reads_per_block=input_reads, aux_reads_per_block=weight_reads,
        work_blocks=output_tiles, output_channels=channels_out,
        traffic_formula="FP16 SAME convolution: input patch ((T+R-1)^2*Cin), "
            "weights (R^2*Cin*Tcout), output (T^2*Tcout); each tensor bytes "
            "rounded up to 64-byte requests. One NPU block is one spatial/output-channel tile.",
        traffic_model={
            "element_bytes": ELEMENT_BYTES, "input_shape": [height, width, channels_in],
            "weight_shape": [kernel, kernel, channels_in, channels_out],
            "output_shape": [output_height, output_width, channels_out],
            "nominal_input_bytes": (height * width * channels_in +
                                    kernel * kernel * channels_in * channels_out)
                * ELEMENT_BYTES,
            "tile_shape": [tile, tile, tile], "input_patch_elements": input_elements,
            "weight_tile_elements": weight_elements, "output_tile_elements": output_elements,
            "input_patch_requests": input_reads, "weight_tile_requests": weight_reads,
            "output_tile_requests": writes, "npu_blocks": output_tiles,
            "flops_per_tile": flops, "peak_flops_per_second": NPU_PEAK_FLOPS,
            "cross_tile_reuse": "none; each block reloads its input patch and weight tile",
        })


def _gemm_defaults():
    m = n = 128
    k = 120
    tile = 16
    a_elements = tile * k
    b_elements = k * tile
    c_elements = tile * tile
    a_reads, b_reads = _requests_for(a_elements), _requests_for(b_elements)
    writes = _requests_for(c_elements)
    output_tiles = _ceil_div(m, tile) * _ceil_div(n, tile)
    flops = 2 * tile * tile * k
    return dict(
        read_streams=("A_tile", "B_tile"), write_streams=("C_tile",),
        input_base=0x400000, aux_base=0x500000, state_base=0x600000,
        output_base=0x600000, region_size=0x1000,
        request_size=REQUEST_BYTES, stride=REQUEST_BYTES,
        burst_count=a_reads + b_reads, read_burst_count=a_reads + b_reads,
        write_burst_count=writes, request_spacing="1ns", max_outstanding=8,
        compute_delay=f"{_compute_delay_ns(flops)}ns", idle_gap="0ns",
        payload_seed=0x6D, traffic_style="gemm",
        dimension0=m, dimension1=n, dimension2=k, kernel_size=1,
        heads=1, tile_size=tile, input_reads_per_block=a_reads,
        aux_reads_per_block=b_reads, work_blocks=output_tiles,
        traffic_formula="FP16 GEMM C[M,N]=A[M,K]B[K,N]: per output tile read "
            "A[Tm,K] and B[K,Tn], then write C[Tm,Tn]; A/B are reloaded for "
            "each output tile (no cross-tile cache reuse).",
        traffic_model={
            "element_bytes": ELEMENT_BYTES, "A_shape": [m, k], "B_shape": [k, n],
            "C_shape": [m, n], "tile_shape": [tile, tile, tile],
            "nominal_input_bytes": (m * k + k * n) * ELEMENT_BYTES,
            "A_tile_elements": a_elements, "B_tile_elements": b_elements,
            "C_tile_elements": c_elements, "A_tile_requests": a_reads,
            "B_tile_requests": b_reads, "C_tile_requests": writes,
            "npu_blocks": output_tiles, "flops_per_tile": flops,
            "peak_flops_per_second": NPU_PEAK_FLOPS,
            "cross_tile_reuse": "none; each output tile reloads A and B tiles",
        })


def _attention_defaults():
    sequence = 128
    hidden = 80
    heads = 5
    head_dim = hidden // heads
    tile = 16
    query_elements = tile * head_dim
    key_value_elements = sequence * head_dim
    output_elements = tile * head_dim
    q_reads = _requests_for(query_elements)
    k_reads = _requests_for(key_value_elements)
    v_reads = _requests_for(key_value_elements)
    writes = _requests_for(output_elements)
    output_tiles = _ceil_div(sequence, tile) * heads
    flops = 4 * tile * sequence * head_dim + 5 * tile * sequence
    return dict(
        read_streams=("Q_tile", "K_sequence", "V_sequence"),
        write_streams=("attention_output",), input_base=0x800000,
        aux_base=0xA00000, state_base=0xC00000, output_base=0xE00000,
        region_size=0x4000, request_size=REQUEST_BYTES, stride=REQUEST_BYTES,
        burst_count=q_reads + k_reads + v_reads,
        read_burst_count=q_reads + k_reads + v_reads,
        write_burst_count=writes, request_spacing="1ns", max_outstanding=8,
        compute_delay=f"{_compute_delay_ns(flops)}ns", idle_gap="0ns",
        payload_seed=0xA7, traffic_style="attention",
        dimension0=sequence, dimension1=hidden, dimension2=head_dim,
        kernel_size=1, heads=heads, tile_size=tile,
        input_reads_per_block=q_reads, aux_reads_per_block=k_reads,
        state_reads_per_block=v_reads, work_blocks=output_tiles,
        traffic_formula="FP16 tiled attention: per head/query tile read Q[Tq,D] "
            "and K[S,D], V[S,D], then write O[Tq,D]; QK^T and probability*V "
            "are included in compute work. K/V are reloaded for each query tile.",
        traffic_model={
            "element_bytes": ELEMENT_BYTES, "sequence_length": sequence,
            "hidden_size": hidden, "heads": heads, "head_dimension": head_dim,
            "tile_shape": [tile, head_dim], "Q_tile_elements": query_elements,
            "nominal_input_bytes": 3 * sequence * hidden * ELEMENT_BYTES,
            "K_sequence_elements": key_value_elements,
            "V_sequence_elements": key_value_elements,
            "output_tile_elements": output_elements,
            "Q_tile_requests": q_reads, "K_sequence_requests": k_reads,
            "V_sequence_requests": v_reads, "output_tile_requests": writes,
            "npu_blocks": output_tiles, "flops_per_tile": flops,
            "peak_flops_per_second": NPU_PEAK_FLOPS,
            "cross_tile_reuse": "none; K/V are reloaded for each query tile",
        })


KERNEL_DEFAULTS = {
    "convolution": _convolution_defaults(),
    "attention": _attention_defaults(),
    "rnn": dict(
        read_streams=("input_timestep", "recurrent_weights", "hidden_state"),
        write_streams=("hidden_output",), input_base=0x900000,
        aux_base=0xA00000, state_base=0xB00000, output_base=0xC00000,
        region_size=0x4000, request_size=64, stride=64, burst_count=4,
        request_spacing="2ns", max_outstanding=8, compute_delay="75ns",
        idle_gap="0ns", payload_seed=0x52, traffic_style="rnn",
        dimension0=128, dimension1=256, dimension2=64, kernel_size=1,
        heads=1, tile_size=16),
    "gemm": _gemm_defaults(),
}


def profile_for_role(role, workload="synthetic", kernel=None, **overrides):
    """Build one role profile for a workload in Configuration A."""
    if role not in ROLES:
        raise ValueError(f"unknown initiator role: {role}")
    if workload not in WORKLOADS:
        raise ValueError(f"unknown workload type: {workload}")
    if workload == "synthetic" or role != "npu":
        return _profile(role, ROLE_DEFAULTS[role], overrides)
    if kernel not in KERNELS:
        raise ValueError(f"unknown AI kernel: {kernel}")
    return _profile(role, KERNEL_DEFAULTS[kernel], overrides)


def profile_for_agent(agent, **overrides):
    """Compatibility resolver for historical role-only manifests."""
    if agent not in ROLES:
        raise ValueError(f"unknown workload agent: {agent}")
    return profile_for_role(agent, **overrides)


def resolve_profiles(workload="synthetic", kernel=None, initiators=None):
    """Return role profiles for the fixed DSP+NPU+MCU Configuration A."""
    initiators = initiators or {}
    unknown = set(initiators) - set(ROLES)
    if unknown:
        raise ValueError("unknown initiator roles: " + ", ".join(sorted(unknown)))
    return {
        role: profile_for_role(role, workload, kernel,
                               **dict(initiators.get(role, {})))
        for role in ROLES
    }
