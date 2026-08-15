"""Create the network topology for the key-rotation-frequency PQC
experiment (thesis: how much does rotating PQC keys more or less often
cost, independently of which KEM/signature algorithm is used).

CORRECTED DESIGN (see README.md for the full rationale): an
earlier version of this suite made "adaptive" mean switching which
KEM/signature algorithm the endpoints use, per trial, via container
restart. That reintroduces exactly the overhead an adaptive scheme is
supposed to avoid, and conflates two independent questions. The actual
adaptive lever this suite now studies is: given a FIXED algorithm, how
often should the client/server redo the full handshake (the only way to
rotate a PQC key -- TLS 1.3's native KeyUpdate only rotates symmetric
traffic keys, it never re-runs the KEM or the signature). Which
algorithm is used is now a constant for the whole experiment, not a
sweep axis.

Consequence: this suite needs NO Docker image of its own. It reuses
`iotsim-pqc-static` / `iotsim/pqc-static` (../../Dockerfiles/pqc_static/,
shared with the static suite) unchanged -- a fixed KEM group + signature
algorithm, baked in at build time, is exactly what this experiment wants
too. Still a SEPARATE GNS3 project from the static suite's `tese_pqc`,
though: this one's router gets reconfigured with different
traffic-shaping profiles and its endpoints get CPU/memory throttled
between trials (see network_profiles.py / device_profiles.py) -- keeping
that isolated from the static suite's own single-shot handshake-cost
benchmark avoids one experiment's conditions leaking into the other's
baseline numbers. "Identical" network baseline (same addressing, same
router config -- see ../../router/network_profiles/clean.sh, shared with
the static suite's create_topology_tese.py) keeps results comparable.

Prerequisites (see README.md):
    make pqc_static                              # builds iotsim/pqc-static (in the static suite's folder)
    python3 create_templates_tese.py             # registers the GNS3 template (also in the static suite's folder)

Run from the `src/tese` directory, with the GNS3 server running:
    (venv) $ python3 create_topology_adaptive_tese.py
"""

import ipaddress
import json
import sys
import time

from pathlib import Path

# gns3utils.py lives in <repo_root>/src, not on sys.path by default from
# this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gns3utils import *

PROJECT_NAME = "tese_pqc_adaptive"
AUTO_CONFIGURE_ROUTER = True

ROUTER_TEMPLATE_NAME = "VyOS 1.3.0"
SWITCH_TEMPLATE_NAME = "Open vSwitch"
# Reused from the static suite -- see module docstring for why this suite
# has no Docker image of its own. Must already be registered (run that
# suite's create_templates_tese.py first).
ENDPOINT_TEMPLATE_NAME = "iotsim-pqc-static"

# The "clean" network profile doubles as the router's initial baseline
# config -- see network_profiles.py and ../../router/network_profiles/clean.sh.
ROUTER_CONFIG_SCRIPT = "../../router/network_profiles/clean.sh"
STATE_FILE = Path("topology_state.json")

CLIENT_ZONE_GATEWAY = "192.168.101.1"
SERVER_ZONE_GATEWAY = "192.168.102.1"
CLIENT_IP = ipaddress.IPv4Interface("192.168.101.10/24")
SERVER_IP = ipaddress.IPv4Interface("192.168.102.10/24")
SERVER_PORT = 4433

check_resources()
check_local_gns3_config()
server = Server(*read_local_gns3_config())

check_server_version(server)

project = get_project_by_name(server, PROJECT_NAME)

if project:
    print(f"Project {PROJECT_NAME} exists. ", project)
else:
    project = create_project(server, PROJECT_NAME, 800, 1200)
    print("Created project ", project)

open_project_if_closed(server, project)

if len(get_all_nodes(server, project)) > 0:
    print("Project is not empty!")
    sys.exit(1)

templates = get_all_templates(server)

router_template_id = get_template_id_from_name(templates, ROUTER_TEMPLATE_NAME)
assert router_template_id, f"Router template '{ROUTER_TEMPLATE_NAME}' not found. Import the VyOS appliance first."
switch_template_id = get_template_id_from_name(templates, SWITCH_TEMPLATE_NAME)
assert switch_template_id, f"Switch template '{SWITCH_TEMPLATE_NAME}' not found. Run create_templates.py first."
endpoint_template_id = get_template_id_from_name(templates, ENDPOINT_TEMPLATE_NAME)
assert endpoint_template_id, (
    f"Endpoint template '{ENDPOINT_TEMPLATE_NAME}' not found. "
    "Run create_templates_tese.py from the STATIC suite first (this suite reuses its image/template)."
)

input("Open the GNS3 project GUI. Press enter to continue...")

############
# TOPOLOGY #
############
#                     pqc-router (VyOS)
#                  eth0 |        | eth1        eth2 (livre)
#                       |        |
#           pqc-switch-client   pqc-switch-server
#                       |        |
#                  pqc-client   pqc-server

coord_router = Position(0, 0)
coord_switch_client = Position(coord_router.x - project.grid_unit * 4, coord_router.y + project.grid_unit * 3)
coord_switch_server = Position(coord_router.x + project.grid_unit * 4, coord_router.y + project.grid_unit * 3)
coord_client = Position(coord_switch_client.x, coord_switch_client.y + project.grid_unit * 3)
coord_server = Position(coord_switch_server.x, coord_switch_server.y + project.grid_unit * 3)

router = create_node(server, project, coord_router.x, coord_router.y, router_template_id, node_name="pqc-router")

switch_client = create_node(server, project, coord_switch_client.x, coord_switch_client.y, switch_template_id, node_name="pqc-switch-client")
switch_server = create_node(server, project, coord_switch_server.x, coord_switch_server.y, switch_template_id, node_name="pqc-switch-server")

create_link(server, project, router["node_id"], 0, switch_client["node_id"], 0)
create_link(server, project, router["node_id"], 1, switch_server["node_id"], 0)

if AUTO_CONFIGURE_ROUTER:
    print(f"Installing {router['name']}")
    hostname, port = get_node_telnet_host_port(server, project, router["node_id"])
    terminal_cmd = f"konsole -e telnet {hostname} {port}"
    start_node(server, project, router["node_id"])
    install_vyos_image_on_node(router["node_id"], hostname, port, pre_exec=terminal_cmd)
    time.sleep(10)
    print(f"Configuring {router['name']} with {ROUTER_CONFIG_SCRIPT}")
    start_node(server, project, router["node_id"])
    configure_vyos_image_on_node(router["node_id"], hostname, port, ROUTER_CONFIG_SCRIPT, pre_exec=terminal_cmd)
    time.sleep(10)

client = create_node(server, project, coord_client.x, coord_client.y, endpoint_template_id, node_name="pqc-client")
server_node = create_node(server, project, coord_server.x, coord_server.y, endpoint_template_id, node_name="pqc-server")

create_link(server, project, switch_client["node_id"], 1, client["node_id"], 0)
create_link(server, project, switch_server["node_id"], 1, server_node["node_id"], 0)

set_node_network_interfaces(server, project, client["node_id"], "eth0", CLIENT_IP, CLIENT_ZONE_GATEWAY)
set_node_network_interfaces(server, project, server_node["node_id"], "eth0", SERVER_IP, SERVER_ZONE_GATEWAY)

# ROLE picks client/server. PQC_KEM_GROUP/PQC_SIG_ALG are NOT set here --
# left at whatever iotsim/pqc-static was built with (default ML-KEM-768 /
# ML-DSA-65, see the static suite's Dockerfile) and never overridden
# afterwards. Unlike the earlier design, run_experiment_tese.py does not
# touch these env vars or restart these nodes between trials -- the only
# thing that varies per trial now is the rotation interval, a client-side
# timing parameter, not container state.
client_env = {"ROLE": "client", "SERVER_HOST": str(SERVER_IP.ip), "SERVER_PORT": str(SERVER_PORT)}
server_env = {"ROLE": "server", "SERVER_PORT": str(SERVER_PORT)}
update_docker_node_environment(server, project, client["node_id"], environment_dict_to_string(client_env))
update_docker_node_environment(server, project, server_node["node_id"], environment_dict_to_string(server_env))

state = {
    "project_name": PROJECT_NAME,
    "project_id": project.id,
    "nodes": {
        "router": {"node_id": router["node_id"], "name": router["name"]},
        "switch_client": {"node_id": switch_client["node_id"], "name": switch_client["name"]},
        "switch_server": {"node_id": switch_server["node_id"], "name": switch_server["name"]},
        "client": {"node_id": client["node_id"], "name": client["name"]},
        "server": {"node_id": server_node["node_id"], "name": server_node["name"]},
    },
    "networks": {
        "client_zone": {"cidr": "192.168.101.0/24", "gateway": CLIENT_ZONE_GATEWAY, "client_ip": str(CLIENT_IP)},
        "server_zone": {"cidr": "192.168.102.0/24", "gateway": SERVER_ZONE_GATEWAY, "server_ip": str(SERVER_IP)},
    },
    "server_port": SERVER_PORT,
    "endpoint_template": ENDPOINT_TEMPLATE_NAME,
    "current_network_profile": "clean",
}
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2)

print(f"\nTopology created. Role -> node_id mapping saved to {STATE_FILE.resolve()}")
print("Endpoints run iotsim-pqc-static (same image as the static suite), one fixed algorithm for the whole experiment.")
print("Next: run_scenario_adaptive_tese.py to bring the network up, then run_experiment_tese.py to sweep rotation intervals.")
