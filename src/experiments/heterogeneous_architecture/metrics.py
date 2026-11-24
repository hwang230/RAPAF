"""Post-run metric bundles for memory targets.

The bundle keeps the native gem5 statistic name beside each normalized value.
Missing statistics are represented explicitly as unsupported rather than zero.
"""

import csv
import json
import os


def _stats(path):
    result = {}
    if not path or not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 2 or fields[0].startswith("#"):
                continue
            try:
                result[fields[0]] = float(fields[1])
            except ValueError:
                continue
    return result


def _metric(stats, native, unit, semantics, estimate=False, transform=None):
    key = native if native in stats else next(
        (candidate for candidate in stats if candidate.endswith("." + native)), None)
    if key is None:
        return {"value": None, "unit": unit, "native_stat": native,
                "semantics": semantics, "estimate": estimate,
                "supported": False,
                "reason": "Statistic unavailable for instantiated target"}
    value = stats[key]
    if transform:
        value = transform(value)
    return {"value": value, "unit": unit, "native_stat": native,
            "semantics": semantics, "estimate": estimate, "supported": True}


def _histogram(stats, native, unit, semantics):
    values = {key[len(native) + 2:]: value for key, value in stats.items()
              if key.startswith(native + "::")}
    if not values:
        return {"value": None, "unit": unit, "native_stat": native,
                "semantics": semantics, "estimate": True,
                "supported": False,
                "reason": "Statistic unavailable for instantiated target"}
    return {"value": values, "unit": unit, "native_stat": native,
            "semantics": semantics, "estimate": True, "supported": True}


def _histogram_bins(value):
    """Return only bin entries from a normalized histogram value."""
    if not isinstance(value, dict):
        return {}
    return {label: count for label, count in value.items()
            if "-" in str(label) or str(label).lower() in
            ("underflow", "overflow")}


def _hist_percentile(stats, native, percentile):
    bins = []
    for key, value in stats.items():
        if not key.startswith(native + "::") or "-" not in key:
            continue
        try:
            low, high = key[len(native) + 2:].split("-", 1)
            bins.append((float(low), float(high), value))
        except ValueError:
            continue
    total = sum(item[2] for item in bins)
    if not total:
        return {"value": None, "unit": "ns", "native_stat": native,
                "semantics": "response-based estimate", "estimate": True,
                "supported": False,
                "reason": "No response samples in latency histogram"}
    target = total * percentile
    cumulative = 0
    for low, high, count in sorted(bins):
        cumulative += count
        if cumulative >= target:
            return {"value": high / 1000.0, "unit": "ns",
                    "native_stat": native,
                    "semantics": "response-based estimate", "estimate": True,
            "supported": True}


class MemoryMetricBundle:
    COMMON = {
        "total_read_bytes": ("totalReadBytes", "bytes", "completed responses"),
        "total_write_bytes": ("totalWrittenBytes", "bytes", "accepted writes"),
        "average_read_bandwidth": ("averageReadBandwidth", "GB/s", "completed responses", lambda x: x / 1e9),
        "average_write_bandwidth": ("averageWriteBandwidth", "GB/s", "accepted writes", lambda x: x / 1e9),
        "read_latency_histogram": ("readLatencyHist", "ticks", "response-based", None),
        "outstanding_reads": ("outstandingReadsHist", "requests", "accepted requests", None),
        "outstanding_writes": ("outstandingWritesHist", "requests", "accepted requests", None),
        "inter_request_time": ("ittReqReq", "ticks", "accepted requests", None),
        "read_transaction_size": ("readTransHist", "bytes", "accepted reads", None),
        "write_transaction_size": ("writeTransHist", "bytes", "accepted writes", None),
        "read_bandwidth_distribution": ("readBandwidthHist", "bytes/s", "completed responses", None),
        "write_bandwidth_distribution": ("writeBandwidthHist", "bytes/s", "accepted writes", None),
    }
    CONTROLLER = {
        "average_read_queue_occupancy": "avgRdQLen",
        "average_write_queue_occupancy": "avgWrQLen",
        "read_queue_occupancy_distribution": "rdQLenPdf",
        "write_queue_occupancy_distribution": "wrQLenPdf",
        "read_queue_full_retries": "numRdRetry",
        "write_queue_full_retries": "numWrRetry",
    }
    DRAM = {
        "average_read_queueing_delay": "avgQLat",
        "average_read_access_latency": "avgMemAccLat",
        "bus_utilization": "busUtil",
        "bus_utilization_read": "busUtilRead",
        "bus_utilization_write": "busUtilWrite",
        "read_row_hit_rate": "readRowHitRate",
        "write_row_hit_rate": "writeRowHitRate",
        "read_bursts_per_bank": "perBankRdBursts",
        "write_bursts_per_bank": "perBankWrBursts",
    }
    POWER = {"total_energy": "totalEnergy", "average_power": "averagePower",
             "power_state_time": "pwrStateTime", "total_idle_time": "totalIdleTime"}

    def __init__(self, stable_name, monitor_path=None, target=None):
        self.stable_name = stable_name
        self.monitor_path = monitor_path or f"system.metrics_{stable_name}"
        self.target = target

    def collect(self, stats_path, start_tick=None, end_tick=None):
        stats = _stats(stats_path)
        prefix = self.monitor_path + "."
        scoped = {key[len(prefix):]: value for key, value in stats.items()
                  if key.startswith(prefix)}
        common = {}
        for name, spec in self.COMMON.items():
            native, unit, semantics, *rest = spec
            if name.endswith("histogram") or name.endswith("distribution"):
                common[name] = _histogram(scoped, native, unit, semantics)
            else:
                common[name] = _metric(scoped, native, unit, semantics,
                                       transform=rest[0] if rest else None)
        common["read_latency_p95"] = _hist_percentile(scoped, "readLatencyHist", .95)
        common["read_latency_p99"] = _hist_percentile(scoped, "readLatencyHist", .99)
        for name, native in (("read_transaction_count", "readTransHist"),
                             ("write_transaction_count", "writeTransHist")):
            samples = scoped.get(native + "::samples")
            common[name] = {"value": samples, "unit": "transactions",
                            "native_stat": native + "::samples",
                            "semantics": "accepted requests",
                            "estimate": False, "supported": samples is not None,
                            **({} if samples is not None else {
                                "reason": "Statistic unavailable for instantiated target"})}
        controller = {name: _metric(stats, native, "count", "controller statistic")
                      for name, native in self.CONTROLLER.items()}
        dram = {name: _metric(stats, native, "native", "DRAM statistic")
                for name, native in self.DRAM.items()}
        power = {name: _metric(stats, native, "native", "dram_only")
                 for name, native in self.POWER.items()}
        interval = {"start_tick": start_tick, "end_tick": end_tick,
                    "elapsed_ticks": ((end_tick - start_tick)
                                      if start_tick is not None and end_tick is not None else None),
                    "phase": "MEASURED_TRAFFIC"}
        return {"common": common, "controller": controller, "dram": dram,
                "power": power, "interval": interval,
                "metadata": {"stable_name": self.stable_name,
                              "monitor_path": self.monitor_path}}


def write_metric_outputs(path_json, path_csv, result):
    with open(path_json, "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    if not path_csv:
        return
    rows = []
    for system, bundle in result.get("memory_systems", {}).items():
        for module in ("common", "controller", "dram", "power"):
            for metric, value in bundle.get(module, {}).items():
                rows.append({"memory_system": system, "channel": "0",
                             "pseudo_channel": "0", "rank": "0",
                             "scope": "memory", "module": module,
                             "metric": metric, **value,
                             "start_tick": bundle.get("interval", {}).get("start_tick"),
                             "end_tick": bundle.get("interval", {}).get("end_tick"),
                             "elapsed_ticks": bundle.get("interval", {}).get("elapsed_ticks")})
    fields = ["memory_system", "channel", "pseudo_channel", "rank", "scope",
              "module", "metric", "value", "unit", "native_stat",
              "semantics", "estimate", "supported", "reason", "start_tick",
              "end_tick", "elapsed_ticks"]
    with open(path_csv, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _configuration_histogram_rows(configurations, metric, module, memory_system):
    """Normalize histogram values into comparable long-form rows."""
    if not hasattr(configurations, "items"):
        configurations = dict(configurations)
    rows = []
    for configuration, result in configurations.items():
        bundle = result
        selected_system = memory_system
        if isinstance(result, dict) and "memory_systems" in result:
            systems = result.get("memory_systems", {})
            selected_system = selected_system or next(iter(systems), None)
            bundle = systems.get(selected_system, {})
        value = bundle.get(module, {}).get(metric, {}) if isinstance(bundle, dict) else {}
        bins = _histogram_bins(value.get("value")) if isinstance(value, dict) else {}
        common = {"configuration": configuration,
                  "memory_system": selected_system,
                  "module": module, "metric": metric,
                  "unit": value.get("unit") if isinstance(value, dict) else None,
                  "native_stat": value.get("native_stat") if isinstance(value, dict) else None,
                  "semantics": value.get("semantics") if isinstance(value, dict) else None,
                  "estimate": value.get("estimate", True) if isinstance(value, dict) else True,
                  "supported": bool(value.get("supported", False)) if isinstance(value, dict) else False}
        if bins:
            rows.extend({**common, "bin": label, "count": count}
                        for label, count in bins.items())
        else:
            rows.append({**common, "bin": None, "count": None,
                         "reason": (value.get("reason", "Histogram has no bins")
                                    if isinstance(value, dict) else
                                    "Histogram metric unavailable")})
    return rows


def write_configuration_histogram(path, configurations, metric="read_latency_histogram",
                                  module="common", memory_system=None):
    """Write comparable histogram bins for configurations in one experiment.

    ``configurations`` may be a mapping of configuration label to a complete
    runner result, or to a single memory-system bundle.  A complete result is
    expected to contain ``memory_systems``; a bundle contains ``common``,
    ``controller``, ``dram``, or ``power`` directly.  The output is long-form
    JSON or CSV (selected by the filename suffix), so multiple configurations
    can be plotted or pivoted without losing the native statistic metadata.
    Unsupported/non-histogram values are emitted with ``supported=false`` and
    a reason rather than silently producing an empty or zero histogram.
    """
    rows = _configuration_histogram_rows(configurations, metric, module,
                                         memory_system)
    if str(path).lower().endswith(".json"):
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(rows, stream, indent=2, sort_keys=True)
        return rows
    fields = ["configuration", "memory_system", "module", "metric", "bin",
              "count", "unit", "native_stat", "semantics", "estimate",
              "supported", "reason"]
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def write_configuration_histogram_svg(path, configurations,
                                      metric="read_latency_histogram",
                                      module="common", memory_system=None):
    """Render a self-contained grouped-bar SVG from configuration histograms.

    Histogram bins are native gem5 buckets.  For latency histograms they are
    displayed in ns on the x axis (gem5 ticks are ps in this prototype).  The
    chart is intentionally dependency-free so it is available on a standard
    gem5 Python installation; the long-form CSV remains the authoritative
    numeric export.
    """
    if not hasattr(configurations, "items"):
        configurations = dict(configurations)
    rows = _configuration_histogram_rows(configurations, metric, module,
                                         memory_system)
    valid = [row for row in rows if row.get("supported") and row.get("bin")]
    if not valid:
        raise ValueError("No supported histogram bins available for SVG output")

    def bin_start(row):
        return int(str(row["bin"]).split("-", 1)[0])

    configurations_order = list(dict.fromkeys(row["configuration"] for row in valid))
    bins = sorted(set(row["bin"] for row in valid),
                  key=lambda label: int(str(label).split("-", 1)[0]))
    counts = {(row["configuration"], row["bin"]): float(row["count"])
              for row in valid}
    max_count = max(counts.values()) or 1.0
    width, height = 1280, 620
    left, right, top, bottom = 88, 32, 72, 128
    plot_width, plot_height = width - left - right, height - top - bottom
    group_width = plot_width / len(bins)
    bar_width = group_width * 0.72 / len(configurations_order)
    colours = ("#356AE6", "#E36B3E", "#2A9D6F", "#9B59B6")

    experiment_configs = [result.get("configuration", {})
                          for result in configurations.values()
                          if isinstance(result, dict) and "configuration" in result]
    agents = list(dict.fromkeys(agent.upper() for config in experiment_configs
                                for agent in config.get(
                                    "initiators", config.get("agents", []))))
    connections = list(dict.fromkeys(config.get("connection")
                                     for config in experiment_configs
                                     if config.get("connection")))
    combination = " + ".join(agents) if agents else "Initiators unspecified"
    connection_label = ", ".join(connections) if connections else "unspecified"

    def esc(value):
        return (str(value).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<style>text{font-family:Arial,sans-serif;fill:#1f2937}.title{font-size:22px;font-weight:600}.axis{font-size:12px}.label{font-size:11px}.legend{font-size:13px}</style>',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="34" class="title">Read request–response latency histogram — {esc(combination)}</text>',
        f'<text x="{left}" y="55" class="axis">Combination: {esc(combination)}; interconnect: {esc(connection_label)}; native CommMonitor response samples; bucket start in ns</text>',
    ]
    for step in range(5):
        value = max_count * step / 4
        y = top + plot_height - plot_height * step / 4
        svg.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#d1d5db"/>')
        svg.append(f'<text x="{left-9}" y="{y+4:.1f}" text-anchor="end" class="axis">{value:g}</text>')
    svg.append(f'<line x1="{left}" y1="{top+plot_height}" x2="{width-right}" y2="{top+plot_height}" stroke="#374151"/>')
    svg.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_height}" stroke="#374151"/>')
    for index, label in enumerate(bins):
        x = left + index * group_width
        for config_index, configuration in enumerate(configurations_order):
            count = counts.get((configuration, label), 0.0)
            bar_height = plot_height * count / max_count
            bar_x = x + group_width * 0.14 + config_index * bar_width
            bar_y = top + plot_height - bar_height
            svg.append(f'<rect x="{bar_x:.2f}" y="{bar_y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" fill="{colours[config_index % len(colours)]}"/>')
        label_ns = bin_start({"bin": label}) / 1000.0
        text_x = x + group_width / 2
        svg.append(f'<text x="{text_x:.2f}" y="{top+plot_height+12}" transform="rotate(55 {text_x:.2f} {top+plot_height+12})" text-anchor="start" class="label">{label_ns:g}</text>')
    svg.append(f'<text x="{left-56}" y="{top + plot_height / 2:.1f}" transform="rotate(-90 {left-56} {top + plot_height / 2:.1f})" text-anchor="middle" class="axis">Responses</text>')
    svg.append(f'<text x="{left + plot_width / 2:.1f}" y="{height-16}" text-anchor="middle" class="axis">Latency bucket start (ns)</text>')
    legend_x = width - right - 210
    for index, configuration in enumerate(configurations_order):
        y = 28 + index * 20
        svg.append(f'<rect x="{legend_x}" y="{y-11}" width="12" height="12" fill="{colours[index % len(colours)]}"/>')
        svg.append(f'<text x="{legend_x+18}" y="{y}" class="legend">{esc(configuration)}</text>')
    svg.append('</svg>')
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(svg) + "\n")
    return rows


def write_histogram(path, configurations, metric="read_latency_histogram",
                    module="common", memory_system=None):
    """Short alias for :func:`write_configuration_histogram`."""
    return write_configuration_histogram(path, configurations, metric, module,
                                         memory_system)
