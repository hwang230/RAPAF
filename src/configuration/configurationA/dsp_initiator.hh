#ifndef __DSP_PROTO_DSP_INITIATOR_HH__
#define __DSP_PROTO_DSP_INITIATOR_HH__

#include "configuration/configurationA/synthetic_initiator_base.hh"
#include "params/DspInitiator.hh"

namespace gem5 {
class DspInitiator : public SyntheticInitiatorBase
{
  public:
    DspInitiator(const DspInitiatorParams &p) : SyntheticInitiatorBase(p) {}
};
}

#endif
