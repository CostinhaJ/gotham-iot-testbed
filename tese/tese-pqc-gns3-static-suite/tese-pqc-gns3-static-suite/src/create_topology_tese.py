"""Create the network topology for the master's thesis experiment
(static PQC vs. adaptive PQC protocol selection).

Builds the network -- one VyOS router separating a "client zone" from a
"server zone", one Open vSwitch switch per zone (also usable as packet
capture points) -- and the two endpoint nodes (client, server), both
using the `iotsim-pqc-static` image: a TLS 1.3 endpoint with a FIXED
KEM group (ML-KEM-768) and signature algorithm (ML-DSA-65) baked into
the image at build time (see ../Dockerfiles/pqc_static/Dockerfile).
Client vs. server behaviour is selected per-node via the ROLE
environment variable set below, not via separate images.

This is the *static* baseline. The *adaptive* protocol-selection model
is a separate step, layered on top of this same network topology later
-- see ../tese/README.md.

Prerequisites (see ../tese/README.md):
    make pqc_static                      # builds iotsim/pqc-static
    python3 create_templates_tese.py     # registers the GNS3 template

Run from the `src/` directory, with the GNS3 server running:
    (venv) $ python3 create_topology_tese.py
"""

import ipaddress
import json
import sys
import time

from pathlib import Path

from gns3utils import *

PROJECT_NAME = "tese_pqc"
AUTO_CONFIGURE_ROUTER = True

# Existing project templates (created by create_templates.py / the router
# appliance import), plus the thesis-specific endpoint template (created
# by create_templates_tese.py). See ../tese/README.md for why these were
# chosen.
ROUTER_TEMPLATE_NAME = "VyOS 1.3.0"
SWITCH_TEMPLATE_NAME = "Open vSwitch"
ENDPOINT_TEMPLATE_NAME = "iotsim-pqc-static"

ROUTER_CONFIG_SCRIPT = "../tese/router_pqc.sh"
STATE_FILE = Path("../tese/topology_state.json")

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

# Templates must already exist in GNS3 (create_templates.py + manual VyOS
# appliance import, as described in the project README).
templates = get_all_templates(server)

router_template_id = get_template_id_from_name(templates, ROUTER_TEMPLATE_NAME)
assert router_template_id, f"Router template '{ROUTER_TEMPLATE_NAME}' not found. Import the VyOS appliance first."
switch_template_id = get_template_id_from_name(templates, SWITCH_TEMPLATE_NAME)
assert switch_template_id, f"Switch template '{SWITCH_TEMPLATE_NAME}' not found. Run create_templates.py first."
endpoint_template_id = get_template_id_from_name(templates, ENDPOINT_TEMPLATE_NAME)
assert endpoint_template_id, f"Endpoint template '{ENDPOINT_TEMPLATE_NAME}' not found. Run create_templates.py first."

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

# router
router = create_node(server, project, coord_router.x, coord_router.y, router_template_id, node_name="pqc-router")

# switches (one per zone)
switch_client = create_node(server, project, coord_switch_client.x, coord_switch_client.y, switch_template_id, node_name="pqc-switch-client")
switch_server = create_node(server, project, coord_switch_server.x, coord_switch_server.y, switch_template_id, node_name="pqc-switch-server")

create_link(server, project, router["node_id"], 0, switch_client["node_id"], 0)
create_link(server, project, router["node_id"], 1, switch_server["node_id"], 0)

# router installation and configuration
if AUTO_CONFIGURE_ROUTER:
    print(f"Installing {router['name']}")
    hostname, port = get_node_telnet_host_port(server, project, router["node_id"])
    terminal_cmd = f"konsole -e telnet {hostname} {port}"
    start_node(server, project, router["node_id"])
    install_vyos_image_on_node(router["node_id"], hostname, port, pre_exec=terminal_cmd)
    # time to close the terminals, else Telnet throws EOF errors
    time.sleep(10)
    print(f"Configuring {router['name']} with {ROUTER_CONFIG_SCRIPT}")
    start_node(server, project, router["node_id"])
    configure_vyos_image_on_node(router["node_id"], hostname, port, ROUTER_CONFIG_SCRIPT, pre_exec=terminal_cmd)
    time.sleep(10)

# endpoint nodes (client / server), same image, role set via environment
client = create_node(server, project, coord_client.x, coord_client.y, endpoint_template_id, node_name="pqc-client")
server_node = create_node(server, project, coord_server.x, coord_server.y, endpoint_template_id, node_name="pqc-server")

create_link(server, project, switch_client["node_id"], 1, client["node_id"], 0)
create_link(server, project, switch_server["node_id"], 1, server_node["node_id"], 0)

set_node_network_interfaces(server, project, client["node_id"], "eth0", CLIENT_IP, CLIENT_ZONE_GATEWAY)
set_node_network_interfaces(server, project, server_node["node_id"], "eth0", SERVER_IP, SERVER_ZONE_GATEWAY)

# Both nodes run the same iotsim-pqc-static image; ROLE picks which side
# of the (fixed, static) TLS suite each one runs -- see
# Dockerfiles/pqc_static/entrypoint.sh. This must happen before the nodes
# are started (update_docker_node_environment only takes effect while the
# node is stopped, same constraint as set_node_network_interfaces above).
client_env = {"ROLE": "client", "SERVER_HOST": str(SERVER_IP.ip), "SERVER_PORT": str(SERVER_PORT)}
server_env = {"ROLE": "server", "SERVER_PORT": str(SERVER_PORT)}
update_docker_node_environment(server, project, client["node_id"], environment_dict_to_string(client_env))
update_docker_node_environment(server, project, server_node["node_id"], environment_dict_to_string(server_env))

#################
# SAVE THE ROLE -> NODE_ID MAPPING
#################
# GNS3's automatic node naming is not reliable enough to identify roles
# later (two nodes created from the same "iotsim-pqc-static" template
# would both get auto-named "iotsim-pqc-static-N", and create_node()'s
# name override is best-effort). run_scenario_tese.py and
# run_benchmark_tese.py read this file instead of guessing from names.
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
    # informational only; the values actually baked into the image are the
    # source of truth (run_benchmark_tese.py reads them back from the
    # running container rather than trusting this copy).
    "endpoint_template": ENDPOINT_TEMPLATE_NAME,
}
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2)

print(f"\nTopology created. Role -> node_id mapping saved to {STATE_FILE.resolve()}")
print("Endpoints run the static PQC suite baked into iotsim-pqc-static (ML-KEM-768 / ML-DSA-65 by default).")
print("Next: run_scenario_tese.py to bring the network up, then run_benchmark_tese.py to measure "
      "the static baseline. The adaptive model is a separate, later step.")
