"""Minimal, single-trial run of the adaptive PQC key-rotation experiment,
derived from run_full_experiment_tese.py.

WHY THIS EXISTS: the full sweep (run_full_experiment_tese.py) is
6 rotation intervals x 4 network profiles x 3 device profiles = 72 trials
by default, each observed for up to 300s, plus ~2 minutes of router
reboot every time the network profile changes -- several hours end to
end (see README.md, "Verificação antes de confiar num sweep completo").
That is the wrong tool for checking that the pipeline itself works:
that GNS3/Docker/tshark are reachable, that the keylog-based TLS 1.3
decryption actually yields a non-zero Certificate size, that the CSV
schema is what a downstream ML step expects. This script is that check
-- it corresponds to the README's own "smoke test em pequena escala"
recommendation (--rotation-intervals 10 --observation-window-s 30
--network-profiles clean --device-profiles unconstrained), packaged as
its own script instead of flags you have to remember to pass to the
full one.

ARCHITECTURE (updated -- see create_topology_edge_tese.py): the topology
is now End Node -- Edge Node -- Router -- Server (no switches; the
switch-based topology's Open vSwitch nodes turned out to not actually be
forwarding frames -- ARP came back "incomplete" on both segments). The
Edge Node performs the handshake now, in place of the old "pqc-client"
node it inherited that role/address from -- everything below execs into
the Edge Node's container the same way the old script exec'd into the
client's. The End Node isn't touched by this script at all yet (no
sensor-simulation logic exists yet, see create_topology_edge_tese.py's
"model placeholder" comments) -- it's only there so the network topology
matches the target architecture; this experiment runner doesn't need it.

WHAT'S DIFFERENT FROM run_full_experiment_tese.py:
  - Exactly ONE trial: one rotation interval, one network profile, one
    device profile -- no sweep, no nested loops.
  - Everything downstream of "pick a trial" is untouched: the same
    warmup + repeated-handshake trial loop, the same ResourceSampler
    (CPU/mem/net), the same per-trial packet capture + keylog-based
    TLS 1.3 decryption, and the same CSV_FIELDS schema as the full
    sweep -- so a minimal run's output can be inspected with the exact
    same expectations (and, if useful, concatenated with a full run's
    CSV later; suite_type/run_id/trial_id still disambiguate rows).
  - Defaults are small on purpose (10s rotation interval, 30s
    observation window -> ~2-3 handshakes) so the whole thing finishes
    in well under a minute of observation time, not counting whatever
    the network/device profile application costs (see below).

NETWORK/DEVICE PROFILES ARE NOT APPLIED BY DEFAULT (unlike the full
sweep). apply_network_profile() needs the router freshly started --
sitting at its login prompt -- so it can push config over telnet; it
calls start_node() itself and assumes that lands it there. But the
documented order is create_topology_edge_tese.py -> boot_scenario_edge.py
(which already starts the router and waits for it to boot) ->
run_*_experiment.py. By the time this script runs, the router is
already up and past its login prompt, so start_node() on an
already-running node is a no-op and the subsequent telnet step waits
for a prompt that will never reappear -- it hangs (not just "slow"; no
output, no progress) instead of failing loudly. Since
create_topology_edge_tese.py already provisions the router with the
'clean' baseline config and containers start with no cgroup limits
(-> DEVICE_PROFILES['unconstrained']), reapplying either by default
here is redundant *and* the network one is actively risky. --network-profile
/ --device-profile still control what's logged in the CSV; pass
--apply-network-profile / --apply-device-profile explicitly if you
actually want this script to touch the router/containers (e.g. right
after a fresh router boot, with no boot_scenario_edge.py run started it)
or to smoke-test the profile-switching machinery itself.

Prerequisites: create_topology_edge_tese.py and boot_scenario_edge.py
must have already been run (GNS3 project up, End Node/Edge Node/Server
running, connectivity validated -- see boot_scenario_edge.py's printed
ping checklist).

    (venv) $ python3 run_minimal_experiment.py [--rotation-interval-s N]
                                                [--observation-window-s N]
                                                [--network-profile P]
                                                [--device-profile P]
                                                [--apply-network-profile]
                                                [--apply-device-profile]
                                                [--output PATH]
"""

import argparse
import csv
import json
import sys
import time
import uuid

from datetime import datetime, timezone
from pathlib import Path

import docker

# gns3utils.py lives in <repo_root>/src, not on sys.path by default from
# this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gns3utils import *

from tese.utils.capture_utils import (
    fetch_and_reset_keylog,
    get_capture_links,
    parse_trial_pcap,
    start_trial_capture,
    stop_trial_capture_and_download,
)
from tese.utils.device_profiles import DEVICE_PROFILES, apply_device_profile
from tese.utils.network_profiles import NETWORK_PROFILES, apply_network_profile
from tese.utils.resource_sampler import ResourceSampler

PROJECT_NAME = "tese_pqc_edge"
STATE_FILE = Path("topology_state.json")
RESULTS_DIR = Path("results")
CAPTURES_DIR = Path("results/captures")
EDGE_KEYLOG_PATH = "/tmp/keylog.txt"
CA_CERT_PATH = "/certs/ca.crt"  # baked into iotsim/pqc-static, see that suite's Dockerfile

# Small on purpose -- see module docstring. 10s over a 30s window yields
# ~2-3 rotations, enough to sanity-check the pipeline without waiting on
# a full 300s observation window.
DEFAULT_ROTATION_INTERVAL_S = 10
DEFAULT_OBSERVATION_WINDOW_S = 30
DEFAULT_NETWORK_PROFILE = "clean"
DEFAULT_DEVICE_PROFILE = "unconstrained"

CSV_FIELDS = [
    "run_id", "trial_id", "suite_type", "kem_group", "sig_alg",
    "rotation_interval_s", "observation_window_s",
    "network_profile", "network_delay_ms", "network_loss_pct", "network_bandwidth_kbit",
    "device_profile", "device_cpu_limit", "device_mem_limit_mb",
    "rotation_index", "ok", "handshake_time_s",
    "trial_rotation_count",
    "trial_cpu_pct_mean", "trial_cpu_pct_max", "trial_mem_mb_mean", "trial_mem_mb_max",
    "trial_net_rx_bytes", "trial_net_tx_bytes",
    "trial_pcap_total_bytes", "trial_clienthello_bytes", "trial_serverhello_bytes",
    "trial_certificate_bytes", "trial_tcp_retransmits", "trial_capture_duration_s",
    "timestamp_utc",
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rotation-interval-s", type=float, default=DEFAULT_ROTATION_INTERVAL_S,
                         help=f"single key-rotation interval in seconds (default: {DEFAULT_ROTATION_INTERVAL_S})")
    parser.add_argument("--observation-window-s", type=float, default=DEFAULT_OBSERVATION_WINDOW_S,
                         help=f"wall-clock duration of the trial (default: {DEFAULT_OBSERVATION_WINDOW_S}s)")
    parser.add_argument("--network-profile", default=DEFAULT_NETWORK_PROFILE, choices=list(NETWORK_PROFILES),
                         help=f"network profile to LOG in the CSV (default: {DEFAULT_NETWORK_PROFILE}). "
                              "Not applied to the router unless --apply-network-profile is also given.")
    parser.add_argument("--device-profile", default=DEFAULT_DEVICE_PROFILE, choices=list(DEVICE_PROFILES),
                         help=f"device profile to LOG in the CSV (default: {DEFAULT_DEVICE_PROFILE}). "
                              "Not applied to the containers unless --apply-device-profile is also given.")
    parser.add_argument("--apply-network-profile", action="store_true",
                         help="actually reconfigure the router for --network-profile before the trial. "
                              "SKIPPED by default: apply_network_profile() expects the router to be freshly "
                              "started (waiting at its login prompt) so it can push config over telnet -- if "
                              "boot_scenario_edge.py already brought the router up (the documented order), "
                              "the router is past that prompt and this call can hang indefinitely instead of "
                              "just being slow. Pass this flag only right after a fresh router boot, or if you "
                              "specifically want to smoke-test the network-profile-switching machinery itself.")
    parser.add_argument("--apply-device-profile", action="store_true",
                         help="actually apply --device-profile's cpu/mem limits to the Edge Node/Server "
                              "containers before the trial (default: off -- containers run "
                              "unconstrained/whatever they already are, which matches "
                              "DEFAULT_DEVICE_PROFILE='unconstrained' anyway).")
    parser.add_argument("--output", type=Path, default=None, help="output CSV path (default: results/minimal_run_<timestamp>.csv)")
    return parser.parse_args()


HANDSHAKE_TIMEOUT_S = 15  # safety net, not a realistic bound -- see pcap analysis in the chat: a
# real handshake here completes in low single-digit milliseconds. This only fires if something is
# genuinely broken (network, server) -- it should never be what actually ends a normal handshake.
#
# Piping "Q\n" into stdin (instead of closing it via `< /dev/null`) is the actual fix for the
# hang: `openssl s_client` recognises a line starting with "Q" typed on stdin as an explicit
# "close the connection and quit" command. EOF on stdin alone (what `< /dev/null` gives it) is NOT
# enough -- confirmed by packet capture: with `< /dev/null`, the handshake (and even a couple of
# post-handshake Application Data messages) completes in ~5ms, then the connection just sits idle
# until `timeout` kills it 15s later. Every one of those was actually a SUCCESSFUL handshake
# reported as a FAILURE -- an artifact of our own client never telling the connection to close, not
# a problem with the network, the server, or the crypto (all confirmed working by the same pcap).


def run_one_handshake(edge_container, server_host: str, server_port: int, kem_group: str):
    """Time a single TLS 1.3 handshake, exec'd into the Edge Node's
    container -- identical to the full sweep's version (see
    run_full_experiment_tese.py for the full rationale on host-side timing
    via docker exec + perf_counter), just against the Edge Node instead of
    the old "pqc-client" node it replaced (see module docstring)."""
    handshake_cmd = [
        "sh", "-c",
        f"printf 'Q\\n' | timeout {HANDSHAKE_TIMEOUT_S} openssl s_client "
        f"-connect {server_host}:{server_port} -CAfile {CA_CERT_PATH} "
        f"-groups {kem_group} -keylogfile {EDGE_KEYLOG_PATH} -quiet",
    ]
    t0 = time.perf_counter()
    exit_code, output = edge_container.exec_run(handshake_cmd)
    elapsed = time.perf_counter() - t0
    if exit_code != 0:
        reason = f" (timed out after {HANDSHAKE_TIMEOUT_S}s -- this should be rare now, see module comment)" if exit_code == 124 else ""
        print(f"    [handshake FAILED] exit_code={exit_code}{reason}\n"
              f"    openssl output: {output.decode(errors='replace')[:2000]!r}")
    return exit_code == 0, elapsed


def run_trial(edge_container, server_host: str, server_port: int, kem_group: str,
              rotation_interval_s: float, observation_window_s: float):
    """One warmup handshake (discarded), then repeated handshakes spaced
    by rotation_interval_s until observation_window_s has elapsed --
    identical logic to the full sweep's run_trial()."""
    run_one_handshake(edge_container, server_host, server_port, kem_group)  # warmup, discarded

    rows = []
    start = time.monotonic()
    rotation_index = 0
    while True:
        time.sleep(rotation_interval_s)
        rotation_index += 1
        ok, elapsed = run_one_handshake(edge_container, server_host, server_port, kem_group)
        print(f"  rotation {rotation_index} (t={time.monotonic() - start:.1f}s)  {'OK' if ok else 'FAIL'}  {elapsed * 1000:.2f} ms")
        rows.append({"rotation_index": rotation_index, "ok": ok, "handshake_time_s": round(elapsed, 6)})
        if time.monotonic() - start >= observation_window_s:
            break
    return rows


def main():
    args = parse_args()

    check_resources()
    check_local_gns3_config()
    server = Server(*read_local_gns3_config())
    check_server_version(server)

    project = get_project_by_name(server, PROJECT_NAME)
    if not project:
        print(f"Project {PROJECT_NAME} does not exist! Run create_topology_edge_tese.py first.")
        sys.exit(1)

    if not STATE_FILE.exists():
        print(f"State file {STATE_FILE} not found. Run create_topology_edge_tese.py first.")
        sys.exit(1)
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    nodes = state["nodes"]
    server_host = state["networks"]["server_zone"]["server_ip"].split("/")[0]
    server_port = state["server_port"]

    docker_client = docker.from_env()
    docker_client.ping()

    edge_container_id = get_node_docker_container_id(server, project, nodes["edge_node"]["node_id"])
    edge_container = docker_client.containers.get(edge_container_id)
    server_container_id = get_node_docker_container_id(server, project, nodes["server"]["node_id"])
    server_container = docker_client.containers.get(server_container_id)
    if edge_container.status != "running":
        print(f"Edge Node container is '{edge_container.status}', not 'running'. Run boot_scenario_edge.py first.")
        sys.exit(1)

    # Fixed for the run -- resolved from the container's actual env, same
    # reasoning as the full sweep: the CSV should reflect what's really
    # running, not what a build-arg said it would be.
    exit_code, env_out = edge_container.exec_run(["sh", "-c", "echo \"$PQC_KEM_GROUP:$PQC_SIG_ALG\""])
    if exit_code != 0:
        print("Could not read PQC_KEM_GROUP/PQC_SIG_ALG from the Edge Node container.")
        sys.exit(1)
    kem_group, sig_alg = env_out.decode().strip().split(":")

    # See get_capture_links() in capture_utils.py: this now resolves the
    # Edge Node<->Router link directly (there's no switch to look it up
    # through anymore), specifically excluding the Edge Node's other link
    # (to the End Node), which carries no PQC/TLS traffic.
    edge_link_ids, server_link_ids = get_capture_links(server, project, state)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.output or (RESULTS_DIR / f"minimal_run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv")
    run_id = str(uuid.uuid4())

    network_profile = args.network_profile
    device_profile = args.device_profile
    net_meta = NETWORK_PROFILES[network_profile]
    dev_meta = DEVICE_PROFILES[device_profile]

    print(f"[minimal experiment] run_id={run_id} algo={kem_group}/{sig_alg}")
    print(f"[minimal experiment] single trial: rotation_interval_s={args.rotation_interval_s} "
          f"network_profile={network_profile} device_profile={device_profile} "
          f"observation_window_s={args.observation_window_s}")
    if not args.apply_network_profile:
        print(f"[minimal experiment] NOT reconfiguring the router -- assuming it's already at '{network_profile}' "
              "(pass --apply-network-profile to force it; see --help for why this is off by default)")
    if not args.apply_device_profile:
        print(f"[minimal experiment] NOT applying cpu/mem limits -- assuming containers are already at "
              f"'{device_profile}' (pass --apply-device-profile to force it)")
    print(f"[minimal experiment] writing rows to {out_path.resolve()} when the trial finishes")

    with open(out_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()

        if args.apply_network_profile:
            apply_network_profile(server, project, nodes["router"]["node_id"], network_profile)
        if args.apply_device_profile:
            apply_device_profile(edge_container, device_profile)
            apply_device_profile(server_container, device_profile)

        trial_id = f"{args.rotation_interval_s}s_{network_profile}_{device_profile}"
        print(f"\n[trial] {trial_id}")

        edge_container.exec_run(["rm", "-f", EDGE_KEYLOG_PATH])
        start_trial_capture(server, project, edge_link_ids, server_link_ids)
        sampler = ResourceSampler(docker_client, edge_container)
        sampler.start()

        trial_rows = run_trial(edge_container, server_host, server_port, kem_group,
                                args.rotation_interval_s, args.observation_window_s)

        resource_metrics = sampler.stop()
        keylog_bytes = fetch_and_reset_keylog(edge_container, EDGE_KEYLOG_PATH)
        edge_pcap = stop_trial_capture_and_download(server, project, edge_link_ids, server_link_ids, CAPTURES_DIR, trial_id)
        pcap_metrics = parse_trial_pcap(edge_pcap, keylog_bytes, CAPTURES_DIR, trial_id)

        timestamp = datetime.now(timezone.utc).isoformat()
        trial_rotation_count = len(trial_rows)
        for row in trial_rows:
            writer.writerow({
                "run_id": run_id,
                "trial_id": trial_id,
                "suite_type": "adaptive_rotation_minimal",
                "kem_group": kem_group,
                "sig_alg": sig_alg,
                "rotation_interval_s": args.rotation_interval_s,
                "observation_window_s": args.observation_window_s,
                "network_profile": network_profile,
                "network_delay_ms": net_meta["delay_ms"],
                "network_loss_pct": net_meta["loss_pct"],
                "network_bandwidth_kbit": net_meta["bandwidth_kbit"],
                "device_profile": device_profile,
                "device_cpu_limit": dev_meta["cpus"],
                "device_mem_limit_mb": dev_meta["mem_limit_mb"],
                "rotation_index": row["rotation_index"],
                "ok": row["ok"],
                "handshake_time_s": row["handshake_time_s"],
                "trial_rotation_count": trial_rotation_count,
                **resource_metrics,
                **pcap_metrics,
                "timestamp_utc": timestamp,
            })
        csv_file.flush()

    print(f"\n[minimal experiment] done. {trial_rotation_count} rotation(s) written to {out_path.resolve()}")
    if pcap_metrics.get("trial_certificate_bytes", 0) == 0:
        print("[minimal experiment] WARNING: trial_certificate_bytes is 0 -- TLS 1.3 keylog decryption "
              "likely isn't working (see capture_utils.py / README.md). Fix this before trusting a full sweep.")


if __name__ == "__main__":
    main()