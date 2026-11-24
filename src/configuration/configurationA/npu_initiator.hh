#ifndef __DSP_PROTO_NPU_INITIATOR_HH__
#define __DSP_PROTO_NPU_INITIATOR_HH__

#include "configuration/configurationA/synthetic_initiator_base.hh"
#include "params/NpuInitiator.hh"

namespace gem5 {
class NpuInitiator : public SyntheticInitiatorBase
{
  public:
    NpuInitiator(const NpuInitiatorParams &p) : SyntheticInitiatorBase(p) {}
};
}

#endif
