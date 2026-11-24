"""Interconnect construction independent of workloads and memory targets."""

from m5.objects import NoncoherentXBar


class NoncoherentXBarInterconnect:
    name = "baseline_noncoherent_xbar"

    def __init__(self, width=16, frontend_latency=1, forward_latency=1,
                 response_latency=1):
        self.width = width
        self.frontend_latency = frontend_latency
        self.forward_latency = forward_latency
        self.response_latency = response_latency
        self.xbar = None

    def build(self, system):
        self.xbar = NoncoherentXBar(width=self.width,
                                    frontend_latency=self.frontend_latency,
                                    forward_latency=self.forward_latency,
                                    response_latency=self.response_latency)
        system.xbar = self.xbar
        return self.xbar

    def connect_upstream(self, port):
        self.xbar.cpu_side_ports = port

    def connect_downstream(self, port):
        self.xbar.mem_side_ports = port

    @property
    def metadata(self):
        return {"name": self.name, "type": "NoncoherentXBar",
                "width_bytes": self.width,
                "frontend_latency": self.frontend_latency,
                "forward_latency": self.forward_latency,
                "response_latency": self.response_latency}


class CreditBasedChipletInterconnect:
    """Timing-modelled credit fabric for topology comparisons.

    The current prototype uses gem5's timing crossbar as the packet switch,
    while exposing the chiplet-level knobs and topology in the resolved
    configuration.  The initiator outstanding limit supplies endpoint credit
    pressure; a native multi-router credit network can replace this adapter
    without changing workloads or memory targets.
    """

    def __init__(self, topology="star", width=16, link_latency=1,
                 router_latency=1, buffer_depth=8):
        if topology not in ("star", "mesh_xy"):
            raise ValueError("chiplet topology must be star or mesh_xy")
        self.topology = topology
        self.width = int(width)
        self.link_latency = int(link_latency)
        self.router_latency = int(router_latency)
        self.buffer_depth = int(buffer_depth)
        self.xbar = None

    def build(self, system):
        self.xbar = NoncoherentXBar(
            width=self.width,
            frontend_latency=self.router_latency,
            forward_latency=self.link_latency,
            response_latency=self.link_latency,
        )
        system.chiplet_interconnect = self.xbar
        return self.xbar

    @property
    def cpu_side_ports(self):
        return self.xbar.cpu_side_ports

    def connect_downstream(self, port):
        self.xbar.mem_side_ports = port

    @property
    def metadata(self):
        return {
            "name": f"credit_chiplet_{self.topology}",
            "type": "CreditBasedChipletInterconnect",
            "implementation": "timing-modelled credit fabric",
            "topology": self.topology,
            "width_bytes": self.width,
            "link_latency": self.link_latency,
            "router_latency": self.router_latency,
            "buffer_depth": self.buffer_depth,
            "flow_control": "endpoint-credit-limited",
        }
