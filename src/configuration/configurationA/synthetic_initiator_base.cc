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

#include "configuration/configurationA/synthetic_initiator_base.hh"

#include <algorithm>
#include <fstream>
#include <iomanip>
#include <numeric>

#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/SyntheticInitiatorBase.hh"
#include "mem/packet.hh"
#include "sim/core.hh"
#include "sim/sim_exit.hh"

namespace gem5
{

SyntheticInitiatorBase::SyntheticInitiatorBaseStats::SyntheticInitiatorBaseStats(statistics::Group *parent)
    : statistics::Group(parent),
      ADD_STAT(attempts, statistics::units::Count::get(), "Send attempts"),
      ADD_STAT(accepted, statistics::units::Count::get(), "Accepted requests"),
      ADD_STAT(completed, statistics::units::Count::get(), "Completed responses"),
      ADD_STAT(rejected, statistics::units::Count::get(), "Rejected send attempts"),
      ADD_STAT(retryCallbacks, statistics::units::Count::get(), "Retry callbacks"),
      ADD_STAT(peakOutstanding, statistics::units::Count::get(), "Peak in-flight requests"),
      ADD_STAT(initAccepted, statistics::units::Count::get(), "Initialization writes accepted"),
      ADD_STAT(initCompleted, statistics::units::Count::get(), "Initialization writes completed"),
      ADD_STAT(verifyAccepted, statistics::units::Count::get(), "Verification reads accepted"),
      ADD_STAT(verifyCompleted, statistics::units::Count::get(), "Verification reads completed"),
      ADD_STAT(workReadsAccepted, statistics::units::Count::get(), "Workload reads accepted"),
      ADD_STAT(workReadsCompleted, statistics::units::Count::get(), "Workload reads completed"),
      ADD_STAT(workWritesAccepted, statistics::units::Count::get(), "Workload writes accepted"),
      ADD_STAT(workWritesCompleted, statistics::units::Count::get(), "Workload writes completed"),
      ADD_STAT(payloadBytesRead, statistics::units::Byte::get(), "Workload bytes read"),
      ADD_STAT(payloadBytesWritten, statistics::units::Byte::get(), "Workload bytes written"),
      ADD_STAT(workReadLatency, statistics::units::Tick::get(), "Workload read response latency"),
      ADD_STAT(workWriteLatency, statistics::units::Tick::get(), "Workload write response latency"),
      ADD_STAT(maxWorkReadLatency, statistics::units::Tick::get(), "Maximum workload read latency"),
      ADD_STAT(maxWorkWriteLatency, statistics::units::Tick::get(), "Maximum workload write latency"),
      ADD_STAT(measuredTicks, statistics::units::Tick::get(), "Measured workload interval"),
      ADD_STAT(blockTicks, statistics::units::Tick::get(), "Sum of block intervals"),
      ADD_STAT(verificationErrors, statistics::units::Count::get(), "Readback mismatches")
{
}

SyntheticInitiatorBase::SyntheticInitiatorBase(const SyntheticInitiatorBaseParams &p)
    : ClockedObject(p), stats(this), system(p.system),
      requestorId(system->getRequestorId(this)), port(name() + ".port", *this),
      driveEvent([this] { drive(); }, name() + ".drive"),
      computeEvent([this] { beginWrites(); }, name() + ".compute"),
      idleEvent([this] { phase = Phase::ReadBurst; scheduleDrive(curTick()); },
                name() + ".idle"),
      timeoutEvent([this] { finish("DSP prototype timeout", 1); },
                   name() + ".timeout"),
      requestSize(p.request_size), readsPerBlock(p.reads_per_block),
      writesPerBlock(p.writes_per_block),
      inputReadsPerBlock(p.input_reads_per_block),
      auxReadsPerBlock(p.aux_reads_per_block),
      stateReadsPerBlock(p.state_reads_per_block),
      requestSpacing(p.request_spacing), computeDelay(p.compute_delay),
      idleGap(p.idle_gap),
      maxOutstanding(p.max_outstanding), numBlocks(p.num_blocks),
      inputBase(p.input_base), auxBase(p.aux_base), stateBase(p.state_base),
      outputBase(p.output_base), regionSize(p.region_size), stride(p.stride),
      payloadSeed(p.payload_seed),
      cacheLineSize(p.cache_line_size), timeout(p.timeout),
      summaryPath(p.summary_path), backend(p.backend), connection(p.connection),
      memorySettings(p.memory_settings), crossbarSettings(p.crossbar_settings),
      trafficStyle(p.traffic_style), dimension0(p.dimension0),
      dimension1(p.dimension1), dimension2(p.dimension2),
      kernelSize(p.kernel_size), heads(p.heads), tileSize(p.tile_size),
      coordinator(p.coordinator)
{
    validateParameters();
}

Port &
SyntheticInitiatorBase::getPort(const std::string &if_name, PortID idx)
{
    if (if_name == "port") {
        panic_if(idx != InvalidPortID, "SyntheticInitiatorBase port is not a vector");
        return port;
    }
    return ClockedObject::getPort(if_name, idx);
}

void
SyntheticInitiatorBase::init()
{
    ClockedObject::init();
    panic_if(!system->isTimingMode(), "%s requires timing memory mode", name());
    panic_if(!port.isConnected(), "%s.port is not connected", name());
}

void
SyntheticInitiatorBase::startup()
{
    scheduleDrive(curTick());
    schedule(timeoutEvent, curTick() + timeout);
}

bool
SyntheticInitiatorBase::InitiatorPort::recvTimingResp(PacketPtr pkt)
{
    return owner.recvTimingResp(pkt);
}

void
SyntheticInitiatorBase::InitiatorPort::recvReqRetry()
{
    owner.recvReqRetry();
}

void
SyntheticInitiatorBase::InitiatorPort::recvRangeChange()
{
    owner.recvRangeChange();
}

void
SyntheticInitiatorBase::scheduleDrive(Tick when)
{
    if (finished)
        return;
    if (!driveEvent.scheduled()) {
        schedule(driveEvent, when);
    } else if (when < driveEvent.when()) {
        reschedule(driveEvent, when, true);
    }
}

void
SyntheticInitiatorBase::drive()
{
    if (finished || outstanding.size() >= maxOutstanding)
        return;

    if (pendingPacket != nullptr && requestBlocked)
        return;

    if (pendingPacket == nullptr && !makePending())
        return;

    if (curTick() < nextSendTick) {
        scheduleDrive(nextSendTick);
        return;
    }

    ++attempts;
    ++stats.attempts;
    if (!port.sendTimingReq(pendingPacket)) {
        requestBlocked = true;
        ++rejected;
        ++stats.rejected;
        DPRINTF(SyntheticInitiatorBase, "%s rejected %s block=%llu index=%llu\n",
                name(), phaseName(), pendingTransaction.block,
                pendingTransaction.index);
        return;
    }

    acceptPending();
    if (outstanding.size() < maxOutstanding)
        scheduleDrive(nextSendTick);
}

bool
SyntheticInitiatorBase::makePending()
{
    Transaction transaction{};
    Addr addr = 0;
    bool write = false;

    switch (phase) {
      case Phase::Initialize:
        if (initIssued == numBlocks * readsPerBlock)
            return false;
        transaction = {Kind::InitWrite, initIssued / readsPerBlock,
                       initIssued % readsPerBlock, 0};
        addr = inputAddr(transaction.block, transaction.index);
        write = true;
        break;
      case Phase::ReadBurst:
        if (readIssued == readIssueLimit()) {
            phase = Phase::WaitReads;
            return false;
        }
        transaction = {Kind::WorkRead,
                       isNpuStream() ? readIssued / readsPerBlock : currentBlock,
                       isNpuStream() ? readIssued % readsPerBlock : readIssued, 0};
        addr = inputAddr(transaction.block, transaction.index);
        break;
      case Phase::WriteBurst:
        if (writeIssued == writeIssueLimit()) {
            phase = Phase::WaitWrites;
            return false;
        }
        transaction = {Kind::WorkWrite,
                       isNpuStream() ? writeIssued / writesPerBlock : currentBlock,
                       isNpuStream() ? writeIssued % writesPerBlock : writeIssued, 0};
        addr = outputAddr(transaction.block, transaction.index);
        write = true;
        break;
      case Phase::Verify:
        if (verifyIssued == numBlocks * writesPerBlock)
            return false;
        transaction = {Kind::VerifyRead, verifyIssued / writesPerBlock,
                       verifyIssued % writesPerBlock, 0};
        addr = outputAddr(transaction.block, transaction.index);
        break;
      default:
        return false;
    }

    pendingTransaction = transaction;
    pendingPacket = makePacket(addr, write, pendingTransaction);
    return true;
}

void
SyntheticInitiatorBase::acceptPending()
{
    requestBlocked = false;
    pendingTransaction.accepted = curTick();
    outstanding.emplace(pendingPacket, pendingTransaction);
    ++accepted;
    ++stats.accepted;
    peakOutstanding = std::max<uint64_t>(peakOutstanding, outstanding.size());
    stats.peakOutstanding = peakOutstanding;

    switch (pendingTransaction.kind) {
      case Kind::InitWrite:
        ++initIssued;
        ++stats.initAccepted;
        break;
      case Kind::WorkRead:
        if (readIssued == 0) {
            blockStart = curTick();
            if (!measurementStarted) {
                measuredStart = curTick();
                measurementStarted = true;
            }
        }
        ++readIssued;
        ++totalWorkReadsAccepted;
        ++stats.workReadsAccepted;
        break;
      case Kind::WorkWrite:
        ++writeIssued;
        ++totalWorkWritesAccepted;
        ++stats.workWritesAccepted;
        stats.payloadBytesWritten += requestSize;
        break;
      case Kind::VerifyRead:
        ++verifyIssued;
        ++stats.verifyAccepted;
        break;
    }
    nextSendTick = curTick() + requestSpacing;
    DPRINTF(SyntheticInitiatorBase, "%s accepted %s block=%llu index=%llu addr=%#x\n",
            name(), phaseName(), pendingTransaction.block,
            pendingTransaction.index, pendingPacket->getAddr());
    pendingPacket = nullptr;
}

bool
SyntheticInitiatorBase::recvTimingResp(PacketPtr pkt)
{
    const auto found = outstanding.find(pkt);
    if (found == outstanding.end())
        panic("%s received an unmatched response at %#x", name(), pkt->getAddr());

    const Transaction transaction = found->second;
    outstanding.erase(found);
    ++completed;
    ++stats.completed;
    responseComplete(transaction, pkt);
    delete pkt;
    return true;
}

void
SyntheticInitiatorBase::recvReqRetry()
{
    ++retries;
    ++stats.retryCallbacks;
    panic_if(pendingPacket == nullptr,
             "%s got recvReqRetry without a retained packet", name());
    requestBlocked = false;
    scheduleDrive(std::max(curTick(), nextSendTick));
}

void
SyntheticInitiatorBase::recvRangeChange()
{
    rangesChanged = true;
}

void
SyntheticInitiatorBase::responseComplete(const Transaction &transaction, PacketPtr pkt)
{
    const Tick latency = curTick() - transaction.accepted;
    switch (transaction.kind) {
      case Kind::InitWrite:
        ++initCompletedCount;
        ++stats.initCompleted;
        if (initCompletedCount == numBlocks * readsPerBlock) {
            phase = Phase::ReadBurst;
            currentBlock = 0;
            readIssued = readCompletedCount = 0;
            scheduleDrive(curTick());
        } else {
            scheduleDrive(curTick());
        }
        break;
      case Kind::WorkRead:
        for (unsigned offset = 0; offset < requestSize; ++offset) {
            if (pkt->getConstPtr<uint8_t>()[offset] !=
                inputByte(pkt->getAddr(), offset)) {
                ++verificationErrors;
                ++stats.verificationErrors;
                finish("input readback mismatch", 1);
                return;
            }
        }
        ++readCompletedCount;
        ++totalWorkReadsCompleted;
        ++stats.workReadsCompleted;
        stats.payloadBytesRead += requestSize;
        workReadLatencySum += latency;
        stats.workReadLatency += latency;
        maxWorkReadLatency = std::max(maxWorkReadLatency, latency);
        stats.maxWorkReadLatency = maxWorkReadLatency;
        if (readCompletedCount == readIssueLimit() &&
            readIssued == readIssueLimit())
            beginCompute();
        else
            scheduleDrive(curTick());
        break;
      case Kind::WorkWrite:
        ++writeCompletedCount;
        ++totalWorkWritesCompleted;
        ++stats.workWritesCompleted;
        workWriteLatencySum += latency;
        stats.workWriteLatency += latency;
        maxWorkWriteLatency = std::max(maxWorkWriteLatency, latency);
        stats.maxWorkWriteLatency = maxWorkWriteLatency;
        if (writeCompletedCount == writeIssueLimit() &&
            writeIssued == writeIssueLimit()) {
            finishBlock();
        } else {
            scheduleDrive(curTick());
        }
        break;
      case Kind::VerifyRead:
        for (unsigned offset = 0; offset < requestSize; ++offset) {
            if (pkt->getConstPtr<uint8_t>()[offset] != outputByte(
                    transaction.block, transaction.index, offset)) {
                ++verificationErrors;
                ++stats.verificationErrors;
                finish("output verification mismatch", 1);
                return;
            }
        }
        ++verifyCompletedCount;
        ++stats.verifyCompleted;
        if (verifyCompletedCount == numBlocks * writesPerBlock) {
            panic_if(!outstanding.empty() || pendingPacket != nullptr,
                     "%s completed with requests still retained", name());
            finish("DSP prototype completed", 0);
        } else {
            scheduleDrive(curTick());
        }
        break;
    }
}

void
SyntheticInitiatorBase::beginCompute()
{
    if (finished ||
        (phase != Phase::ReadBurst && phase != Phase::WaitReads)) {
        return;
    }
    phase = Phase::Compute;
    schedule(computeEvent, curTick() + computeDelay);
}

void
SyntheticInitiatorBase::beginWrites()
{
    if (finished)
        return;
    phase = Phase::WriteBurst;
    writeIssued = writeCompletedCount = 0;
    scheduleDrive(curTick());
}

void
SyntheticInitiatorBase::finishBlock()
{
    measuredEnd = curTick();
    const Tick block_time = curTick() - blockStart;
    blockTimes.push_back(block_time);
    stats.blockTicks += block_time;
    if (isNpuStream()) {
        currentBlock = numBlocks;
        beginVerify();
        return;
    }
    ++currentBlock;
    if (currentBlock == numBlocks) {
        beginVerify();
        return;
    }
    readIssued = readCompletedCount = 0;
    phase = Phase::Idle;
    schedule(idleEvent, curTick() + idleGap);
}

void
SyntheticInitiatorBase::beginVerify()
{
    phase = Phase::Verify;
    verifyIssued = verifyCompletedCount = 0;
    if (measurementStarted) {
        stats.measuredTicks = measuredEnd - measuredStart;
    }
    scheduleDrive(curTick());
}

void
SyntheticInitiatorBase::finish(const std::string &reason, int exit_code)
{
    if (finished)
        return;
    finished = true;
    phase = Phase::Done;
    if (timeoutEvent.scheduled())
        deschedule(timeoutEvent);
    writeSummary(reason);
    if (coordinator)
        coordinator->agentFinished(name(), exit_code);
    else
        exitSimLoop(reason, exit_code);
}

PacketPtr
SyntheticInitiatorBase::makePacket(Addr addr, bool write,
                         const Transaction &transaction)
{
    auto request = std::make_shared<Request>(addr, requestSize, 0, requestorId);
    PacketPtr packet = new Packet(request, write ? MemCmd::WriteReq : MemCmd::ReadReq);
    packet->allocate();
    if (write) {
        std::vector<uint8_t> data(requestSize);
        for (unsigned offset = 0; offset < requestSize; ++offset) {
            data[offset] = transaction.kind == Kind::InitWrite ?
                inputByte(addr, offset) :
                outputByte(transaction.block, transaction.index, offset);
        }
        packet->setData(data.data());
    }
    return packet;
}

uint8_t
SyntheticInitiatorBase::inputByte(Addr addr, unsigned offset) const
{
    return static_cast<uint8_t>(((addr >> 2) + offset * 17 + payloadSeed) & 0xff);
}

uint8_t
SyntheticInitiatorBase::outputByte(uint64_t block, uint64_t index, unsigned offset) const
{
    return static_cast<uint8_t>((block * 31 + index * 13 + offset * 7 +
                                 payloadSeed + 0xa5) &
                                0xff);
}

Addr
SyntheticInitiatorBase::inputAddr(uint64_t block, uint64_t index) const
{
    if (!isAiKernel())
        return inputBase + (block * readsPerBlock + index) * requestSize;

    if (inputReadsPerBlock || auxReadsPerBlock || stateReadsPerBlock) {
        if (index < inputReadsPerBlock)
            return inputBase + block * regionSize + index * stride;
        index -= inputReadsPerBlock;
        if (index < auxReadsPerBlock)
            return auxBase + block * regionSize + index * stride;
        index -= auxReadsPerBlock;
        if (index < stateReadsPerBlock)
            return stateBase + block * regionSize + index * stride;
        panic("%s AI input index exceeds configured stream read counts", name());
    }
    const unsigned streams = inputStreamCount();
    const unsigned stream = index % streams;
    const unsigned streamIndex = index / streams;
    const Addr base = stream == 0 ? inputBase :
        (stream == 1 ? auxBase : stateBase);
    return base + block * regionSize + streamIndex * stride;
}

Addr
SyntheticInitiatorBase::outputAddr(uint64_t block, uint64_t index) const
{
    if (!isAiKernel())
        return outputBase + (block * writesPerBlock + index) * requestSize;
    return outputBase + (block * regionSize) + index * stride;
}

void
SyntheticInitiatorBase::validateParameters() const
{
    panic_if(requestSize == 0 || requestSize > cacheLineSize ||
             cacheLineSize == 0 || cacheLineSize % requestSize != 0,
             "request_size must be a nonzero divisor of cache_line_size");
    panic_if(readsPerBlock == 0 || writesPerBlock == 0 || numBlocks == 0,
             "reads_per_block, writes_per_block, and num_blocks must be positive");
    panic_if(maxOutstanding == 0 || requestSpacing == 0 || timeout == 0,
             "max_outstanding, request_spacing, and timeout must be positive");
    panic_if(inputBase % cacheLineSize != 0 || outputBase % cacheLineSize != 0 ||
             (isAiKernel() && (auxBase % cacheLineSize != 0 ||
                                stateBase % cacheLineSize != 0)),
             "data-region bases must be cache-line aligned");
    panic_if(regionSize == 0 || stride < requestSize,
             "region_size must be positive and stride must cover request_size");
    const unsigned streams = inputStreamCount();
    const unsigned streamReads[] = {inputReadsPerBlock, auxReadsPerBlock,
                                     stateReadsPerBlock};
    const unsigned configuredReads = inputReadsPerBlock + auxReadsPerBlock +
                                     stateReadsPerBlock;
    panic_if(configuredReads != 0 && configuredReads != readsPerBlock,
             "per-stream AI read counts must sum to reads_per_block");
    panic_if((streams < 3 && stateReadsPerBlock != 0) ||
             (streams < 2 && auxReadsPerBlock != 0),
             "per-stream read count configured for an unused AI stream");
    panic_if(isAiKernel() && (dimension0 == 0 || dimension1 == 0 ||
                              dimension2 == 0 || tileSize == 0),
             "AI-kernel dimensions and tile_size must be positive");
    panic_if(trafficStyle != "dsp_block" && trafficStyle != "npu_stream" &&
             trafficStyle != "mcu_control" && trafficStyle != "convolution" &&
             trafficStyle != "attention" && trafficStyle != "gemm" &&
             trafficStyle != "rnn",
             "unknown traffic_style");
    panic_if(isAiKernel() && streams == 0, "AI kernel must have input streams");

    const Addr output_end = isAiKernel() ?
        outputBase + (numBlocks - 1) * regionSize +
            (writesPerBlock - 1) * stride + requestSize :
        outputBase + numBlocks * writesPerBlock * requestSize;
    const auto overlaps = [](Addr first, Addr firstEnd, Addr second,
                             Addr secondEnd) {
        return first < secondEnd && second < firstEnd;
    };
    if (isAiKernel()) {
        const Addr bases[] = {inputBase, auxBase, stateBase};
        const unsigned usedBases = streams;
        for (unsigned stream = 0; stream < usedBases; ++stream) {
            const unsigned requests = configuredReads ? streamReads[stream] :
                (readsPerBlock + usedBases - 1 - stream) / usedBases;
            if (requests == 0)
                continue;
            const Addr input_end = bases[stream] +
                (numBlocks - 1) * regionSize +
                (requests - 1) * stride + requestSize;
            panic_if(overlaps(bases[stream], input_end, outputBase, output_end),
                     "input and output regions must not overlap");
            for (unsigned other = 0; other < stream; ++other) {
                const unsigned other_requests = configuredReads ?
                    streamReads[other] :
                    (readsPerBlock + usedBases - 1 - other) / usedBases;
                if (other_requests == 0)
                    continue;
                const Addr other_end = bases[other] +
                    (numBlocks - 1) * regionSize +
                    (other_requests - 1) * stride + requestSize;
                panic_if(overlaps(bases[stream], input_end, bases[other],
                                  other_end),
                         "AI input regions must not overlap");
            }
        }
    } else {
        const Addr input_end = inputBase +
            numBlocks * readsPerBlock * requestSize;
        panic_if(overlaps(inputBase, input_end, outputBase, output_end),
                 "input and output regions must not overlap");
    }
}

bool
SyntheticInitiatorBase::isNpuStream() const
{
    return trafficStyle == "npu_stream";
}

bool
SyntheticInitiatorBase::isAiKernel() const
{
    return trafficStyle == "convolution" || trafficStyle == "attention" ||
           trafficStyle == "gemm" || trafficStyle == "rnn";
}

unsigned
SyntheticInitiatorBase::inputStreamCount() const
{
    if (trafficStyle == "attention" || trafficStyle == "rnn")
        return 3;
    if (trafficStyle == "convolution" || trafficStyle == "gemm")
        return 2;
    return 1;
}

uint64_t
SyntheticInitiatorBase::readIssueLimit() const
{
    return isNpuStream() ? numBlocks * readsPerBlock : readsPerBlock;
}

uint64_t
SyntheticInitiatorBase::writeIssueLimit() const
{
    return isNpuStream() ? numBlocks * writesPerBlock : writesPerBlock;
}

const char *
SyntheticInitiatorBase::phaseName() const
{
    switch (phase) {
      case Phase::Initialize: return "INITIALIZE";
      case Phase::ReadBurst:
        return isAiKernel() ? "LOAD_TILES" :
               (isNpuStream() ? "READ_STREAM" : "READ_BURST");
      case Phase::WaitReads: return "WAIT_READS";
      case Phase::Compute: return "COMPUTE";
      case Phase::WriteBurst:
        return isAiKernel() ? "STORE_TILES" :
               (isNpuStream() ? "WRITE_STREAM" : "WRITE_BURST");
      case Phase::WaitWrites: return "WAIT_WRITES";
      case Phase::Idle: return "IDLE";
      case Phase::Verify: return "VERIFY";
      case Phase::Done: return "DONE";
    }
    return "UNKNOWN";
}

void
SyntheticInitiatorBase::writeSummary(const std::string &reason) const
{
    std::ofstream out(summaryPath);
    if (!out.is_open()) {
        warn("%s could not write summary to %s", name(), summaryPath);
        return;
    }
    const Tick measured = measurementStarted ? measuredEnd - measuredStart : 0;
    const uint64_t bytes_read = totalWorkReadsCompleted * requestSize;
    const uint64_t bytes_written = totalWorkWritesCompleted * requestSize;
    const uint64_t payload = bytes_read + bytes_written;
    const double bandwidth = measured ?
        static_cast<double>(payload) * sim_clock::Frequency / measured : 0.0;
    const Tick total_blocks = std::accumulate(blockTimes.begin(), blockTimes.end(),
                                               Tick(0));
    const Tick max_block = blockTimes.empty() ? 0 :
        *std::max_element(blockTimes.begin(), blockTimes.end());
    out << "{\n"
        << "  \"gem5_revision\": \"f5c5a6e\",\n"
        << "  \"backend\": \"" << backend << "\",\n"
        << "  \"connection\": \"" << connection << "\",\n"
        << "  \"memory_settings\": \"" << memorySettings << "\",\n"
        << "  \"crossbar_settings\": \"" << crossbarSettings << "\",\n"
        << "  \"traffic_style\": \"" << trafficStyle << "\",\n"
        << "  \"exit_reason\": \"" << reason << "\",\n"
        << "  \"verification_passed\": "
        << (verificationErrors == 0 && reason == "DSP prototype completed")
        << ",\n"
        << "  \"parameters\": {\n"
        << "    \"request_size\": " << requestSize << ",\n"
        << "    \"reads_per_block\": " << readsPerBlock << ",\n"
        << "    \"writes_per_block\": " << writesPerBlock << ",\n"
        << "    \"input_reads_per_block\": " << inputReadsPerBlock << ",\n"
        << "    \"aux_reads_per_block\": " << auxReadsPerBlock << ",\n"
        << "    \"state_reads_per_block\": " << stateReadsPerBlock << ",\n"
        << "    \"request_spacing_ticks\": " << requestSpacing << ",\n"
        << "    \"compute_delay_ticks\": " << computeDelay << ",\n"
        << "    \"idle_gap_ticks\": " << idleGap << ",\n"
        << "    \"max_outstanding\": " << maxOutstanding << ",\n"
        << "    \"num_blocks\": " << numBlocks << ",\n"
        << "    \"input_base\": " << inputBase << ",\n"
        << "    \"aux_base\": " << auxBase << ",\n"
        << "    \"state_base\": " << stateBase << ",\n"
        << "    \"output_base\": " << outputBase << ",\n"
        << "    \"region_size\": " << regionSize << ",\n"
        << "    \"stride\": " << stride << ",\n"
        << "    \"payload_seed\": " << payloadSeed << ",\n"
        << "    \"dimension0\": " << dimension0 << ",\n"
        << "    \"dimension1\": " << dimension1 << ",\n"
        << "    \"dimension2\": " << dimension2 << ",\n"
        << "    \"kernel_size\": " << kernelSize << ",\n"
        << "    \"heads\": " << heads << ",\n"
        << "    \"tile_size\": " << tileSize << "\n"
        << "  },\n"
        << "  \"requests\": {\n"
        << "    \"attempts\": " << attempts << ",\n"
        << "    \"accepted\": " << accepted << ",\n"
        << "    \"completed\": " << completed << ",\n"
        << "    \"rejected_attempts\": " << rejected << ",\n"
        << "    \"retry_callbacks\": " << retries << ",\n"
        << "    \"peak_outstanding\": " << peakOutstanding << ",\n"
        << "    \"init_accepted\": " << initIssued << ",\n"
        << "    \"init_completed\": " << initCompletedCount << ",\n"
        << "    \"work_reads_accepted\": " << totalWorkReadsAccepted << ",\n"
        << "    \"work_reads_completed\": " << totalWorkReadsCompleted << ",\n"
        << "    \"work_writes_accepted\": " << totalWorkWritesAccepted << ",\n"
        << "    \"work_writes_completed\": " << totalWorkWritesCompleted << ",\n"
        << "    \"verify_accepted\": " << verifyIssued << ",\n"
        << "    \"verify_completed\": " << verifyCompletedCount << "\n"
        << "  },\n"
        << "  \"payload\": {\n"
        << "    \"bytes_read\": " << bytes_read << ",\n"
        << "    \"bytes_written\": " << bytes_written << ",\n"
        << "    \"bandwidth_Bps\": " << std::setprecision(17) << bandwidth
        << "\n  },\n"
        << "  \"latency_ticks\": {\n"
        << "    \"mean_read\": "
        << (totalWorkReadsCompleted ?
            workReadLatencySum / totalWorkReadsCompleted : 0)
        << ",\n"
        << "    \"max_read\": " << maxWorkReadLatency << ",\n"
        << "    \"mean_write\": "
        << (totalWorkWritesCompleted ?
            workWriteLatencySum / totalWorkWritesCompleted : 0)
        << ",\n"
        << "    \"max_write\": " << maxWorkWriteLatency << "\n"
        << "  },\n"
        << "  \"measurement\": {\n"
        << "    \"elapsed_ticks\": " << measured << ",\n"
        << "    \"mean_block_ticks\": "
        << (blockTimes.empty() ? 0 : total_blocks / blockTimes.size()) << ",\n"
        << "    \"max_block_ticks\": " << max_block << ",\n"
        << "    \"block_ticks\": [";
    for (size_t index = 0; index < blockTimes.size(); ++index) {
        if (index)
            out << ", ";
        out << blockTimes[index];
    }
    out << "]\n  },\n"
        << "  \"verification_errors\": " << verificationErrors << "\n"
        << "}\n";
}

} // namespace gem5
