"""Tear down the adaptive-suite scenario: stop any lingering capture,
reset the router to the unshaped 'clean' network profile, and stop all
5 nodes in reverse start order.

The static suite's run_scenario_tese.py has the equivalent of this as a
commented-out, non-callable block at the bottom of the file. Here it's a
real, standalone script so a sweep (run_experiment_tese.py) can be
followed by a clean, reproducible teardown instead of manual cleanup --
in particular, resetting the router avoids leaving it mid-shaped for
whatever runs against this project next.

Run from the `src/` directory:
    (venv) $ python3 teardown_tese.py
"""

import json
import sys

from pathlib import Path

# See create_templates_adaptive_tese.py's comment: gns3utils.py lives in
# <repo_root>/src, not on sys.path by default from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from gns3utils import *

from capture_utils import get_capture_links
from network_profiles import apply_network_profile

PROJECT_NAME = "tese_pqc_adaptive"
STATE_FILE = Path("../tese/topology_state.json")

check_local_gns3_config()
server = Server(*read_local_gns3_config())
check_server_version(server)

project = get_project_by_name(server, PROJECT_NAME)
if not project:
    print(f"Project {PROJECT_NAME} does not exist, nothing to tear down.")
    sys.exit(0)

open_project_if_closed(server, project)

if not STATE_FILE.exists():
    print(f"State file {STATE_FILE} not found, nothing to tear down.")
    sys.exit(0)
with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)
nodes = state["nodes"]

print("Stopping any in-progress capture...")
client_link_ids, server_link_ids = get_capture_links(server, project, state)
stop_capture(server, project, client_link_ids)
stop_capture(server, project, server_link_ids)

print("Resetting router to the 'clean' (unshaped) network profile...")
apply_network_profile(server, project, nodes["router"]["node_id"], "clean")

for role in ("client", "server", "switch_client", "switch_server", "router"):
    print(f"Stopping {nodes[role]['name']}")
    stop_node(server, project, nodes[role]["node_id"])

print("\nTeardown complete: all nodes stopped, router reset to 'clean'.")
