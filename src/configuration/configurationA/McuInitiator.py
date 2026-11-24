from m5.objects.SyntheticInitiatorBase import SyntheticInitiatorBase


class McuInitiator(SyntheticInitiatorBase):
    """MCU control/descriptor initiator with an independent identity."""

    type = "McuInitiator"
    cxx_header = "configuration/configurationA/mcu_initiator.hh"
    cxx_class = "gem5::McuInitiator"
