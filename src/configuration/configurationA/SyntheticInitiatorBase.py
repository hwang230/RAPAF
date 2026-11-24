from m5.objects.ClockedObject import ClockedObject
from m5.params import *
from m5.proxy import *


class SyntheticInitiatorBase(ClockedObject):
    """Shared timing/retry engine for synthetic memory initiators."""

    type = "SyntheticInitiatorBase"
    abstract = True
    cxx_header = "configuration/configurationA/synthetic_initiator_base.hh"
    cxx_class = "gem5::SyntheticInitiatorBase"

    system = Param.System(Parent.any, "System containing this initiator")
    port = RequestPort("Timing request port to the memory hierarchy")
    request_size = Param.Unsigned(64, "Bytes in every request")
    reads_per_block = Param.Unsigned(8, "Sequential input reads per block")
    writes_per_block = Param.Unsigned(8, "Sequential output writes per block")
    input_reads_per_block = Param.Unsigned(0, "Explicit input-stream reads per block")
    aux_reads_per_block = Param.Unsigned(0, "Explicit auxiliary-stream reads per block")
    state_reads_per_block = Param.Unsigned(0, "Explicit state-stream reads per block")
    request_spacing = Param.Latency("1ns", "Minimum accepted-send spacing")
    compute_delay = Param.Latency("100ns", "Delay after each read burst")
    idle_gap = Param.Latency("1us", "Delay after each output-write burst")
    max_outstanding = Param.Unsigned(1, "Maximum accepted requests in flight")
    num_blocks = Param.Unsigned(20, "Finite number of work blocks")
    input_base = Param.Addr(0x1000, "Aligned input-region base address")
    aux_base = Param.Addr(0x100000, "Auxiliary input/weight-region base address")
    state_base = Param.Addr(0x200000, "State-region base address")
    output_base = Param.Addr(0x8000, "Aligned output-region base address")
    region_size = Param.Unsigned(0x1000, "Bytes reserved per input stream")
    stride = Param.Unsigned(64, "Byte stride between logical requests")
    payload_seed = Param.Unsigned(0xd5, "Deterministic payload seed")
    cache_line_size = Param.Unsigned(64, "Maximum request/cache-line size")
    timeout = Param.Latency("10ms", "Whole-prototype watchdog")
    summary_path = Param.String("initiator_summary.json", "JSON result file path")
    backend = Param.String("simple", "Configuration metadata only")
    connection = Param.String("direct", "Configuration metadata only")
    memory_settings = Param.String("", "Memory configuration metadata")
    crossbar_settings = Param.String("none", "Crossbar configuration metadata")
    coordinator = Param.TrafficRunCoordinator(NULL, "Optional multi-agent coordinator")
    traffic_style = Param.String(
        "dsp_block",
        "dsp_block, npu_stream, mcu_control, or an NPU kernel traffic style",
    )
    dimension0 = Param.Unsigned(128, "Primary kernel dimension")
    dimension1 = Param.Unsigned(128, "Secondary kernel dimension")
    dimension2 = Param.Unsigned(64, "Tertiary kernel dimension")
    kernel_size = Param.Unsigned(3, "Convolution kernel size")
    heads = Param.Unsigned(8, "Attention head count")
    tile_size = Param.Unsigned(16, "AI-kernel tile size")
