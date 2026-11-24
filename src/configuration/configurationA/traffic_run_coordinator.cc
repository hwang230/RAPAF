#include "configuration/configurationA/traffic_run_coordinator.hh"

#include "sim/sim_exit.hh"

namespace gem5 {
TrafficRunCoordinator::TrafficRunCoordinator(const TrafficRunCoordinatorParams &p)
    : ClockedObject(static_cast<const ClockedObjectParams &>(p)),
      expectedAgents(p.expected_agents), timeout(p.timeout),
      timeoutEvent([this] {
          if (!done) { done = true; exitSimLoop("traffic coordinator timeout", 1); }
      }, name() + ".timeout")
{
}

void TrafficRunCoordinator::startup()
{
    schedule(timeoutEvent, curTick() + timeout);
}

void TrafficRunCoordinator::agentFinished(const std::string &, int exitCode)
{
    if (done || exitCode != 0)
        return;
    if (++finishedAgents >= expectedAgents) {
        done = true;
        if (timeoutEvent.scheduled())
            deschedule(timeoutEvent);
        exitSimLoop("all agents completed", 0);
    }
}
}
