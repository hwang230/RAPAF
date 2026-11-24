#ifndef __DSP_PROTO_MCU_INITIATOR_HH__
#define __DSP_PROTO_MCU_INITIATOR_HH__

#include "configuration/configurationA/synthetic_initiator_base.hh"
#include "params/McuInitiator.hh"

namespace gem5 {
class McuInitiator : public SyntheticInitiatorBase
{
  public:
    McuInitiator(const McuInitiatorParams &p) : SyntheticInitiatorBase(p) {}
};
}

#endif
