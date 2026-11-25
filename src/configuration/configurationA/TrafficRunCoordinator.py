from m5.params import *
from m5.proxy import Parent
from m5.objects.ClockedObject import ClockedObject


class TrafficRunCoordinator(ClockedObject):
    type = "TrafficRunCoordinator"
    cxx_header = "configuration/configurationA/traffic_run_coordinator.hh"
    cxx_class = "gem5::TrafficRunCoordinator"
    system = Param.System(Parent.any, "Containing system")
    expected_agents = Param.Unsigned(1, "Number of enabled initiators")
