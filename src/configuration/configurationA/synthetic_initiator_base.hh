/*
 * Copyright (c) 2026 Hugh Wang
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 * redistributions of source code must retain the above copyright notice, this
 * list of conditions and the following disclaimer; redistributions in binary
 * form must reproduce the above copyright notice, this list of conditions and
 * the following disclaimer in the documentation and/or other materials
 * provided with the distribution; neither the name of the copyright holders
 * nor the names of its contributors may be used to endorse or promote products
 * derived from this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
 * LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
 * CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

#ifndef __DSP_PROTO_SYNTHETIC_INITIATOR_BASE_HH__
#define __DSP_PROTO_SYNTHETIC_INITIATOR_BASE_HH__

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "base/statistics.hh"
#include "mem/port.hh"
#include "params/SyntheticInitiatorBase.hh"
#include "configuration/configurationA/traffic_run_coordinator.hh"
#include "sim/clocked_object.hh"
#include "sim/system.hh"

namespace gem5
{

class SyntheticInitiatorBase : public ClockedObject
{
  private:
    enum class Phase {
        Initialize, ReadBurst, WaitReads, Compute, WriteBurst, WaitWrites,
        Idle, Verify, Done
    };

    enum class Kind { InitWrite, WorkRead, WorkWrite, VerifyRead };

    struct Transaction
    {
        Kind kind;
        uint64_t block;
        uint64_t index;
        Tick accepted;
    };

    class InitiatorPort : public RequestPort
    {
      private:
        SyntheticInitiatorBase &owner;

      public:
        InitiatorPort(const std::string &name, SyntheticInitiatorBase &owner)
            : RequestPort(name), owner(owner)
        {}

      protected:
        bool recvTimingResp(PacketPtr pkt) override;
        void recvReqRetry() override;
        void recvRangeChange() override;
    };

    struct SyntheticInitiatorBaseStats : public statistics::Group
    {
        SyntheticInitiatorBaseStats(statistics::Group *parent);
        statistics::Scalar attempts;
        statistics::Scalar accepted;
        statistics::Scalar completed;
        statistics::Scalar rejected;
        statistics::Scalar retryCallbacks;
        statistics::Scalar peakOutstanding;
        statistics::Scalar initAccepted;
        statistics::Scalar initCompleted;
        statistics::Scalar verifyAccepted;
        statistics::Scalar verifyCompleted;
        statistics::Scalar workReadsAccepted;
        statistics::Scalar workReadsCompleted;
        statistics::Scalar workWritesAccepted;
        statistics::Scalar workWritesCompleted;
        statistics::Scalar payloadBytesRead;
        statistics::Scalar payloadBytesWritten;
        statistics::Scalar workReadLatency;
        statistics::Scalar workWriteLatency;
        statistics::Scalar maxWorkReadLatency;
        statistics::Scalar maxWorkWriteLatency;
        statistics::Scalar p50WorkReadLatency;
        statistics::Scalar p95WorkReadLatency;
        statistics::Scalar p99WorkReadLatency;
        statistics::Scalar p50WorkWriteLatency;
        statistics::Scalar p95WorkWriteLatency;
        statistics::Scalar p99WorkWriteLatency;
        statistics::Scalar measuredTicks;
        statistics::Scalar blockTicks;
        statistics::Scalar verificationErrors;
    } stats;

    void drive();
    void scheduleDrive(Tick when);
    bool makePending();
    void acceptPending();
    void updateWatchdog(bool madeProgress);
    void watchdogExpired();
    bool recvTimingResp(PacketPtr pkt);
    void recvReqRetry();
    void recvRangeChange();
    void responseComplete(const Transaction &transaction, PacketPtr pkt);
    void beginCompute();
    void beginWrites();
    void finishBlock();
    void beginVerify();
    void finish(const std::string &reason, int exit_code);
    void writeSummary(const std::string &reason);
    PacketPtr makePacket(Addr addr, bool write, const Transaction &transaction);
    uint8_t inputByte(Addr addr, unsigned offset) const;
    uint8_t outputByte(uint64_t block, uint64_t index, unsigned offset) const;
    Addr inputAddr(uint64_t block, uint64_t index) const;
    Addr outputAddr(uint64_t block, uint64_t index) const;
    void validateParameters() const;
    const char *phaseName() const;
    bool isNpuStream() const;
    bool isAiKernel() const;
    unsigned inputStreamCount() const;
    uint64_t readIssueLimit() const;
    uint64_t writeIssueLimit() const;

    System *const system;
    const RequestorID requestorId;
    InitiatorPort port;
    EventFunctionWrapper driveEvent;
    EventFunctionWrapper computeEvent;
    EventFunctionWrapper idleEvent;
    EventFunctionWrapper timeoutEvent;

    const unsigned requestSize;
    const unsigned readsPerBlock;
    const unsigned writesPerBlock;
    const unsigned inputReadsPerBlock;
    const unsigned auxReadsPerBlock;
    const unsigned stateReadsPerBlock;
    const Tick requestSpacing;
    const Tick computeDelay;
    const Tick idleGap;
    const unsigned maxOutstanding;
    const uint64_t numBlocks;
    const Addr inputBase;
    const Addr auxBase;
    const Addr stateBase;
    const Addr outputBase;
    const unsigned regionSize;
    const unsigned stride;
    const unsigned payloadSeed;
    const unsigned cacheLineSize;
    const Tick timeout;
    const std::string summaryPath;
    const std::string backend;
    const std::string connection;
    const std::string memorySettings;
    const std::string crossbarSettings;
    const std::string trafficStyle;
    const unsigned dimension0;
    const unsigned dimension1;
    const unsigned dimension2;
    const unsigned kernelSize;
    const unsigned heads;
    const unsigned tileSize;
    TrafficRunCoordinator *const coordinator;

    Phase phase = Phase::Initialize;
    PacketPtr pendingPacket = nullptr;
    Transaction pendingTransaction{};
    std::unordered_map<PacketPtr, Transaction> outstanding;
    Tick nextSendTick = 0;
    Tick lastProgressTick = 0;
    Tick measuredStart = 0;
    Tick measuredEnd = 0;
    Tick blockStart = 0;
    uint64_t currentBlock = 0;
    uint64_t initIssued = 0;
    uint64_t initCompletedCount = 0;
    uint64_t readIssued = 0;
    uint64_t readCompletedCount = 0;
    uint64_t writeIssued = 0;
    uint64_t writeCompletedCount = 0;
    uint64_t totalWorkReadsAccepted = 0;
    uint64_t totalWorkReadsCompleted = 0;
    uint64_t totalWorkWritesAccepted = 0;
    uint64_t totalWorkWritesCompleted = 0;
    uint64_t verifyIssued = 0;
    uint64_t verifyCompletedCount = 0;
    uint64_t attempts = 0;
    uint64_t accepted = 0;
    uint64_t completed = 0;
    uint64_t rejected = 0;
    uint64_t retries = 0;
    uint64_t peakOutstanding = 0;
    uint64_t verificationErrors = 0;
    uint64_t workReadLatencySum = 0;
    uint64_t workWriteLatencySum = 0;
    Tick maxWorkReadLatency = 0;
    Tick maxWorkWriteLatency = 0;
    std::vector<Tick> workReadLatencySamples;
    std::vector<Tick> workWriteLatencySamples;
    std::vector<Tick> blockTimes;
    bool requestBlocked = false;
    bool rangesChanged = false;
    bool measurementStarted = false;
    bool finished = false;

  public:
    SyntheticInitiatorBase(const SyntheticInitiatorBaseParams &p);

    Port &getPort(const std::string &if_name,
                  PortID idx = InvalidPortID) override;
    void init() override;
    void startup() override;
};

} // namespace gem5

#endif // __DSP_PROTO_SYNTHETIC_INITIATOR_BASE_HH__
