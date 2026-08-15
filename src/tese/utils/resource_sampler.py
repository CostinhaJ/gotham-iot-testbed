"""Background CPU/memory sampling for one container, for the duration of
one trial (see run_experiment_tese.py).

docker stats has ~1s granularity, far coarser than a single handshake
(sub-second to low-seconds), so samples are only meaningful aggregated
over a whole trial's observation window (warmup + however many
rotations that interval fits in it), not attributed to individual
handshakes -- see the dataset schema note in README.md.

CPU% and memory are point-in-time gauges, so mean/max over the trial
window is meaningful for them. Network I/O counters are cumulative since
container start (not since trial start), so mean/max of the raw counter
would be meaningless -- instead, ResourceSampler records one snapshot at
start() and one at stop(), and reports the DELTA over the trial window.
"""

import threading
import time

from typing import Dict, Optional


def _cpu_percent(stats: Dict) -> Optional[float]:
    """Same formula the `docker stats` CLI uses."""
    try:
        cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        system_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
    except (KeyError, TypeError):
        return None
    if system_delta <= 0 or cpu_delta < 0:
        return None
    num_cpus = stats["cpu_stats"].get("online_cpus") or len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
    return (cpu_delta / system_delta) * num_cpus * 100.0


def _mem_mb(stats: Dict) -> Optional[float]:
    try:
        return stats["memory_stats"]["usage"] / (1024 * 1024)
    except (KeyError, TypeError):
        return None


def _net_bytes(stats: Dict) -> Optional[Dict[str, int]]:
    networks = stats.get("networks") or {}
    if not networks:
        return None
    rx = sum(iface.get("rx_bytes", 0) for iface in networks.values())
    tx = sum(iface.get("tx_bytes", 0) for iface in networks.values())
    return {"rx_bytes": rx, "tx_bytes": tx}


class ResourceSampler:
    """Samples one container's CPU%/memory in a background thread between
    start() and stop(); reports mean/max CPU and memory, and the network
    byte delta, over that window."""

    def __init__(self, docker_client, container, poll_interval_s: float = 1.0):
        self._api = docker_client.api
        self._container_id = container.id
        self._poll_interval_s = poll_interval_s
        self._cpu_samples = []
        self._mem_samples = []
        self._net_start: Optional[Dict[str, int]] = None
        self._net_end: Optional[Dict[str, int]] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                stats = self._api.stats(self._container_id, stream=False)
            except Exception:
                break
            cpu = _cpu_percent(stats)
            mem = _mem_mb(stats)
            if cpu is not None:
                self._cpu_samples.append(cpu)
            if mem is not None:
                self._mem_samples.append(mem)
            net = _net_bytes(stats)
            if net is not None:
                self._net_end = net
                if self._net_start is None:
                    self._net_start = net
            self._stop_event.wait(self._poll_interval_s)

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> Dict:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval_s * 3)

        cpu_mean = sum(self._cpu_samples) / len(self._cpu_samples) if self._cpu_samples else 0.0
        cpu_max = max(self._cpu_samples) if self._cpu_samples else 0.0
        mem_mean = sum(self._mem_samples) / len(self._mem_samples) if self._mem_samples else 0.0
        mem_max = max(self._mem_samples) if self._mem_samples else 0.0

        if self._net_start is not None and self._net_end is not None:
            net_rx = max(0, self._net_end["rx_bytes"] - self._net_start["rx_bytes"])
            net_tx = max(0, self._net_end["tx_bytes"] - self._net_start["tx_bytes"])
        else:
            net_rx = net_tx = 0

        return {
            "trial_cpu_pct_mean": round(cpu_mean, 2),
            "trial_cpu_pct_max": round(cpu_max, 2),
            "trial_mem_mb_mean": round(mem_mean, 2),
            "trial_mem_mb_max": round(mem_max, 2),
            "trial_net_rx_bytes": net_rx,
            "trial_net_tx_bytes": net_tx,
        }
