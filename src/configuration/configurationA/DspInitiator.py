from m5.objects.SyntheticInitiatorBase import SyntheticInitiatorBase


class DspInitiator(SyntheticInitiatorBase):
    """DSP workload adapter with an independent requestor identity."""

    type = "DspInitiator"
    cxx_header = "configuration/configurationA/dsp_initiator.hh"
    cxx_class = "gem5::DspInitiator"
