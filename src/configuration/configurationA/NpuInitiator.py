from m5.objects.SyntheticInitiatorBase import SyntheticInitiatorBase


class NpuInitiator(SyntheticInitiatorBase):
    """NPU tile-stream initiator with an independent SimObject identity."""

    type = "NpuInitiator"
    cxx_header = "configuration/configurationA/npu_initiator.hh"
    cxx_class = "gem5::NpuInitiator"
