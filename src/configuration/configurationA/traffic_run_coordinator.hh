#ifndef __DSP_PROTO_TRAFFIC_RUN_COORDINATOR_HH__
#define __DSP_PROTO_TRAFFIC_RUN_COORDINATOR_HH__

#include "params/TrafficRunCoordinator.hh"
#include "sim/clocked_object.hh"

namespace gem5 {
class TrafficRunCoordinator : public ClockedObject
{
  private:
    const unsigned expectedAgents;
    unsigned finishedAgents = 0;
    bool done = false;
  public:
    TrafficRunCoordinator(const TrafficRunCoordinatorParams &p);
    void agentFinished(const std::string &reason, int exitCode);
};
}

#endif
