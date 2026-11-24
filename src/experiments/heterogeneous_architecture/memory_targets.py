"""Memory target builders, independent from initiators and workloads."""

from m5.objects import (
    AddrRange,
    CommMonitor,
    DDR3_1600_8x8,
    DDR4_2400_8x8,
    DDR5_4400_4x8,
    HBM_2000_4H_1x64,
    HBMCtrl,
    MemCtrl,
    SimpleMemory,
)


class MemoryTarget:
    stable_name = "memory"

    def __init__(self, system, memory_range, sample_period="1ms"):
        self.system = system
        self.memory_range = memory_range
        self.sample_period = sample_period
        self.monitor = None
        self.memory = None

    def attach_monitor(self):
        # Parent.any on CommMonitor resolves the enclosing System; passing the
        # system explicitly creates a self-parenting SimObject graph in gem5.
        self.monitor = CommMonitor(sample_period=self.sample_period)
        setattr(self.system, f"metrics_{self.stable_name}", self.monitor)
        self.monitor.mem_side_port = self.memory.port
        return self.monitor

    @property
    def upstream_port(self):
        return self.monitor.cpu_side_port if self.monitor else self.memory.port

    @property
    def metadata(self):
        return {"name": self.stable_name, "capacity": str(self.memory_range),
                "channels": 1, "pseudo_channels": 1}


class SimpleMemoryTarget(MemoryTarget):
    stable_name = "sram"

    def __init__(self, system, memory_range, latency="30ns",
                 bandwidth="12.8GiB/s", sample_period="1ms"):
        super().__init__(system, memory_range, sample_period)
        self.memory = SimpleMemory(range=memory_range, latency=latency,
                                   bandwidth=bandwidth)
        system.memory = self.memory
        self.latency = latency
        self.bandwidth = bandwidth

    @property
    def metadata(self):
        result = super().metadata
        result.update({"interface": "SimpleMemory", "latency": self.latency,
                       "bandwidth": self.bandwidth})
        return result


class SingleChannelDramTarget(MemoryTarget):
    interface = None
    stable_name = "dram"
    interface_name = "DRAM"

    def __init__(self, system, memory_range, sample_period="1ms"):
        super().__init__(system, memory_range, sample_period)
        self.memory = MemCtrl(dram=self.interface())
        self.memory.dram.range = memory_range
        system.memory = self.memory

    @property
    def metadata(self):
        result = super().metadata
        result.update({"interface": self.interface_name, "controller": "MemCtrl"})
        return result


class SingleChannelDDR3Target(SingleChannelDramTarget):
    stable_name = "ddr3"
    interface = DDR3_1600_8x8
    interface_name = "DDR3_1600_8x8"


class SingleChannelDDR4Target(SingleChannelDramTarget):
    stable_name = "ddr4"
    interface = DDR4_2400_8x8
    interface_name = "DDR4_2400_8x8"


class SingleChannelDDR5Target(SingleChannelDramTarget):
    stable_name = "ddr5"
    interface = DDR5_4400_4x8
    interface_name = "DDR5_4400_4x8"


class HBM2Target(MemoryTarget):
    stable_name = "hbm2"

    def __init__(self, system, memory_range, sample_period="1ms"):
        super().__init__(system, memory_range, sample_period)
        if memory_range.size() < 2:
            raise ValueError("HBM2 requires at least a 2-byte address range")
        half_size = memory_range.size() // 2
        first_range = AddrRange(memory_range.start, size=half_size)
        second_range = AddrRange(memory_range.start + half_size,
                                 size=memory_range.size() - half_size)
        first = HBM_2000_4H_1x64()
        second = HBM_2000_4H_1x64()
        first.range = first_range
        second.range = second_range
        self.memory = HBMCtrl(dram=first, dram_2=second)
        system.memory = self.memory

    @property
    def metadata(self):
        result = super().metadata
        result.update({"interface": "HBM_2000_4H_1x64",
                       "controller": "HBMCtrl", "channels": 1,
                       "pseudo_channels": 2})
        return result


class FutureHBMTarget(HBM2Target):
    """Backward-compatible name for callers of the old placeholder."""

    stable_name = "hbm"
