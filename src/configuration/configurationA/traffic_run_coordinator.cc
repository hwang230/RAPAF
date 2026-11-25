#include "configuration/configurationA/traffic_run_coordinator.hh"

#include "sim/sim_exit.hh"

namespace gem5 {

TrafficRunCoordinator::TrafficRunCoordinator(const TrafficRunCoordinatorParams &p)
    : ClockedObject(p), expectedAgents(p.expected_agents)
{
}

void TrafficRunCoordinator::agentFinished(const std::string &reason, int exitCode)
{
    if (done)
        return;

    if (exitCode != 0) {
        done = true;
        exitSimLoop(reason, exitCode);
        return;
    }

    if (++finishedAgents >= expectedAgents) {
        done = true;
        exitSimLoop("all agents completed", 0);
    }
}

} // namespace gem5
