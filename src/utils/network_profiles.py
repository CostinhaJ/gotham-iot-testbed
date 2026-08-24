"""Network condition profiles for the adaptive-suite sweep.

Each profile is a full VyOS config script under
../../router/network_profiles/<name>.sh (see clean.sh for why they're full
configs, not deltas) plus the metadata below, logged into the dataset so
each trial's row records the actual delay/loss/bandwidth applied, not
just a profile name (see README.md, dataset schema).

Reconfiguring the router is expensive (reboot required both before and
after -- see apply_network_profile()), so run_experiment_tese.py's sweep
must treat network profile as the OUTERMOST loop.
"""

import sys
import time

from pathlib import Path

# gns3utils.py lives in <repo_root>/src, not on sys.path by default from
# this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from gns3utils import configure_vyos_image_on_node, get_node_telnet_host_port, start_node

PROFILES_DIR = Path(__file__).resolve().parents[3] / "router" / "network_profiles"

# delay_ms / loss_pct / bandwidth_kbit are the values actually written into
# the corresponding .sh script -- keep these in sync by hand if the script
# is edited. bandwidth_kbit is None for "clean" (unshaped).
NETWORK_PROFILES = {
    "clean": {"script": PROFILES_DIR / "clean.sh", "delay_ms": 0, "loss_pct": 0.0, "bandwidth_kbit": None},
    "constrained": {"script": PROFILES_DIR / "constrained.sh", "delay_ms": 200, "loss_pct": 0.0, "bandwidth_kbit": 250},
    "lossy": {"script": PROFILES_DIR / "lossy.sh", "delay_ms": 50, "loss_pct": 7.0, "bandwidth_kbit": None},
    "high_latency": {"script": PROFILES_DIR / "high_latency.sh", "delay_ms": 600, "loss_pct": 0.0, "bandwidth_kbit": 1000},
}

# Boot time budget: same figure used by run_scenario_tese.py /
# run_scenario_adaptive_tese.py for the router's first boot.
ROUTER_BOOT_WAIT_S = 60


def apply_network_profile(server, project, router_node_id: str, profile_name: str) -> None:
    """Reconfigure the router with the given network profile.

    configure_vyos_image_on_node() (see gns3utils.py) requires a freshly
    booted router (it waits for a login prompt) and powers the router off
    when it's done -- so this starts the router, waits for boot, pushes
    the profile's full config, then starts it again and waits for boot a
    second time so the endpoints have a router to talk to afterwards.
    """
    if profile_name not in NETWORK_PROFILES:
        raise ValueError(f"Unknown network profile '{profile_name}'. Known: {sorted(NETWORK_PROFILES)}")

    script_path = str(NETWORK_PROFILES[profile_name]["script"])
    hostname, port = get_node_telnet_host_port(server, project, router_node_id)

    print(f"[network_profiles] applying '{profile_name}' ({script_path})")
    start_node(server, project, router_node_id)
    time.sleep(ROUTER_BOOT_WAIT_S)
    configure_vyos_image_on_node(router_node_id, hostname, port, script_path)

    start_node(server, project, router_node_id)
    time.sleep(ROUTER_BOOT_WAIT_S)
    print(f"[network_profiles] '{profile_name}' applied, router back up")