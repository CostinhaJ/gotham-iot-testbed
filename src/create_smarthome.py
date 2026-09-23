"""Create iot simulation topology (Tese Costa Simples) - SMART HOME, variant B.

Variant B: the home MQTT broker is LOCAL to the house. It stays on the
west-zone switch (szone) together with the IoT client, both on
192.168.17.0/24 with gateway 192.168.17.1 (rzone / router_loc1.sh).
Only DNS, NTP and the TLS broker remain in the 192.168.0.0/20 cloud.
"""

import configparser
import ipaddress
import sys
import time

from gns3utils import *

PROJECT_NAME = "gotham_scenario"
AUTO_CONFIGURE_ROUTERS = True

check_resources()
check_local_gns3_config()
server = Server(*read_local_gns3_config())

check_server_version(server)

project = get_project_by_name(server, PROJECT_NAME)

if project:
    print(f"Project {PROJECT_NAME} exists. ", project)
else:
    project = create_project(server, PROJECT_NAME, 5000, 7500, 15)
    print("Created project ", project)

open_project_if_closed(server, project)

if len(get_all_nodes(server, project)) > 0:
    print("Project is not empty!")
    sys.exit(1)

# Create the templates manually using the GNS3 GUI
# get templates
templates = get_all_templates(server)

# get template ids
router_template_id = get_template_id_from_name(templates, "VyOS 1.3.0")
assert router_template_id
switch_template_id = get_template_id_from_name(templates, "Open vSwitch")
assert switch_template_id
DNS_template_id = get_template_id_from_name(templates, "iotsim-dns")
assert DNS_template_id
certificates_template_id = get_template_id_from_name(templates, "iotsim-certificates")
assert certificates_template_id
NTP_template_id = get_template_id_from_name(templates, "iotsim-ntp")
assert NTP_template_id
mqtt_broker_tls_template_id = get_template_id_from_name(templates, "iotsim-mqtt-broker-tls")
assert mqtt_broker_tls_template_id
building_monitor_template_id = get_template_id_from_name(templates, "iotsim-building-monitor")
assert building_monitor_template_id
sensor_template_id = get_template_id_from_name(templates, "iotsim-sensor")
assert sensor_template_id
sensor_tls_template_id = get_template_id_from_name(templates, "iotsim-sensor-tls")
assert sensor_tls_template_id
debug_client_template_id = get_template_id_from_name(templates, "iotsim-debug-client")
assert debug_client_template_id

# read project configuration file
sim_config = configparser.ConfigParser()
with open("../iot-sim.config", "r", encoding="utf-8") as cf:
    # include fake section header 'main'
    sim_config.read_string(f"[main]\n{cf.read()}")
    sim_config = sim_config["main"]


input("Open the GNS3 project GUI. Press enter to continue...")

############
# TOPOLOGY #
############
# Coordinates:
#
#            ^
#  --        | Y -    +-
#            |
#            |
#  X -       |(0,0)   X +
# <----------+---------->
#            |
#            |
#            |
#            |
#  -+        v Y +     ++

coord_rnorth = Position(1000, -1700)
coord_rwest = Position(coord_rnorth.x - project.grid_unit * 2, coord_rnorth.y + project.grid_unit * 4)

####################
# backbone routers #
####################

rnorth = create_node(server, project, coord_rnorth.x, coord_rnorth.y, router_template_id)
rwest = create_node(server, project, coord_rwest.x, coord_rwest.y, router_template_id)


create_link(server, project, rnorth["node_id"], 1, rwest["node_id"], 1)

# router installation and configuration. TODO in parallel?
backbone_routers = [rnorth, rwest]
backbone_configs = ["../router/backbone/router_north.sh",
                    "../router/backbone/router_west.sh"]

if AUTO_CONFIGURE_ROUTERS:
    for router_node, router_config in zip(backbone_routers, backbone_configs):
        print(f"Installing {router_node['name']}")
        hostname, port = get_node_telnet_host_port(server, project, router_node["node_id"])
        terminal_cmd = f"konsole -e telnet {hostname} {port}"
        start_node(server, project, router_node["node_id"])
        install_vyos_image_on_node(router_node["node_id"], hostname, port, pre_exec=terminal_cmd)
        # time to close the terminals, else Telnet throws EOF errors
        time.sleep(10)
        print(f"Configuring {router_node['name']} with {router_config}")
        start_node(server, project, router_node["node_id"])
        configure_vyos_image_on_node(router_node["node_id"], hostname, port, router_config, pre_exec=terminal_cmd)
        time.sleep(10)

#####################
# backbone switches #
#####################

coord_snorth = Position(coord_rnorth.x, coord_rnorth.y - project.grid_unit * 2)
coord_swest = Position(coord_rwest.x - project.grid_unit * 10, coord_rwest.y)

snorth = create_node(server, project, coord_snorth.x, coord_snorth.y, switch_template_id)
swest = create_node(server, project, coord_swest.x, coord_swest.y, switch_template_id)

create_link(server, project, rnorth["node_id"], 0, snorth["node_id"], 0)
create_link(server, project, rwest["node_id"], 0, swest["node_id"], 0)

# west zone routers and switches
routers_west_zone = []
switches_west_zone = []
coords_west_zone = []
switch_freeport = 1

#for i in [-10, 10]:
coord = Position(coord_swest.x + project.grid_unit * 1, coord_swest.y + project.grid_unit * 3) # i em vez de 1
rzone = create_node(server, project, coord.x, coord.y, router_template_id)
create_link(server, project, rzone["node_id"], 1, swest["node_id"], switch_freeport)
switch_freeport += 1
coord = Position(coord.x, coord.y + project.grid_unit * 2)
szone = create_node(server, project, coord.x, coord.y, switch_template_id)
create_link(server, project, rzone["node_id"], 0, szone["node_id"], 0)
routers_west_zone.append(rzone)
switches_west_zone.append(szone)
coords_west_zone.append(coord)

# router installation and configuration
# only one zone router now -> only router_loc1.sh (LAN 192.168.17.0/24)
rwest_configs = ["../router/locations/router_loc1.sh"]
if AUTO_CONFIGURE_ROUTERS:
    for router_node, router_config in zip(routers_west_zone, rwest_configs):
        print(f"Installing {router_node['name']}")
        hostname, port = get_node_telnet_host_port(server, project, router_node["node_id"])
        terminal_cmd = f"konsole -e telnet {hostname} {port}"
        start_node(server, project, router_node["node_id"])
        install_vyos_image_on_node(router_node["node_id"], hostname, port, pre_exec=terminal_cmd)
        # time to close the terminals, else Telnet throws EOF errors
        time.sleep(10)
        print(f"Configuring {router_node['name']} with {router_config}")
        start_node(server, project, router_node["node_id"])
        configure_vyos_image_on_node(router_node["node_id"], hostname, port, router_config, pre_exec=terminal_cmd)
        time.sleep(10)


lab_nameserver = sim_config["LAB_DNS_IPADDR"]


#######
# DNS #
#######

coord_cloud_snorth = Position(coord_snorth.x + project.grid_unit * 8, coord_snorth.y - project.grid_unit * 2)
cloud_snorth = create_node(server, project, coord_cloud_snorth.x, coord_cloud_snorth.y, switch_template_id)
create_link(server, project, snorth["node_id"], 1, cloud_snorth["node_id"], 0)

dns = create_node(server, project, coord_cloud_snorth.x - project.grid_unit * 1, coord_cloud_snorth.y - project.grid_unit * 2, DNS_template_id)
create_link(server, project, cloud_snorth["node_id"], 1, dns["node_id"], 0)
set_node_network_interfaces(server, project, dns["node_id"], "eth0", ipaddress.IPv4Interface(f"{lab_nameserver}/20"), "192.168.0.1", "127.0.0.1")


#######
# NTP #
#######

NTP_CLOUD_NAME = (f"ntp.{sim_config['LOCAL_DOMAIN']}", "192.168.0.3")

ntp = create_node(server, project, coord_cloud_snorth.x + project.grid_unit * 1, coord_cloud_snorth.y - project.grid_unit * 2, NTP_template_id)
create_link(server, project, cloud_snorth["node_id"], 2, ntp["node_id"], 0)
set_node_network_interfaces(server, project, ntp["node_id"], "eth0", ipaddress.IPv4Interface(f"{NTP_CLOUD_NAME[1]}/20"), "192.168.0.1", lab_nameserver)


######################
# Secure MQTT broker #
######################

MQTT_CLOUD_TLS_NAME = (sim_config["MQTT_TLS_BROKER_CN"], "192.168.0.4")
HOME_BROKER_PLAIN_NAME = (f"broker.home.{sim_config['LOCAL_DOMAIN']}", "192.168.17.2")
HOME_STREAMSERVER_NAME = (f"ipcam.home.{sim_config['LOCAL_DOMAIN']}", "192.168.2.2")

mqtt_cloud_tls = create_node(server, project, coord_cloud_snorth.x + project.grid_unit * 3, coord_cloud_snorth.y - project.grid_unit * 2, mqtt_broker_tls_template_id)
create_link(server, project, cloud_snorth["node_id"], 3, mqtt_cloud_tls["node_id"], 0)
set_node_network_interfaces(server, project, mqtt_cloud_tls["node_id"], "eth0", ipaddress.IPv4Interface(f"{MQTT_CLOUD_TLS_NAME[1]}/20"), "192.168.0.1", lab_nameserver)

home_plain = create_node(server, project, coords_west_zone[0].x - project.grid_unit * 1, coords_west_zone[0].y + project.grid_unit * 2, building_monitor_template_id)
create_link(server, project, szone["node_id"], 2, home_plain["node_id"], 0)
set_node_network_interfaces(server, project, home_plain["node_id"], "eth0",  ipaddress.IPv4Interface("192.168.17.10/24"), "192.168.17.1", lab_nameserver)

# Ligação ao Edge ao broker MQTT
env = environment_string_to_dict(get_docker_node_environment(server, project, home_plain["node_id"]))
env["MQTT_BROKER_ADDR"] = MQTT_CLOUD_TLS_NAME[0]
env["TLS"] = "True"
env["NTP_SERVER"] = NTP_CLOUD_NAME[0]
update_docker_node_environment(server, project, home_plain["node_id"], environment_dict_to_string(env))

sensor_client = create_node(server, project, coords_west_zone[0].x - project.grid_unit * 1, coords_west_zone[0].y + project.grid_unit * 4, sensor_template_id)
create_link(server, project, szone["node_id"], 3, sensor_client["node_id"], 0)
set_node_network_interfaces(server, project, sensor_client["node_id"], "eth0",  ipaddress.IPv4Interface("192.168.17.11/24"), "192.168.17.1", lab_nameserver)
env = environment_string_to_dict(get_docker_node_environment(server, project, sensor_client["node_id"]))
env["COAP_ADDR_LIST"] = "192.168.17.11"
env["PSK"] = "True"
update_docker_node_environment(server, project, sensor_client["node_id"], environment_dict_to_string(env))

#-----------------------------------------------------------------------------------------------------
  

EXTRA_HOSTS = {NTP_CLOUD_NAME[0]: NTP_CLOUD_NAME[1],
               HOME_BROKER_PLAIN_NAME[0]: HOME_BROKER_PLAIN_NAME[1],
               MQTT_CLOUD_TLS_NAME[0]: MQTT_CLOUD_TLS_NAME[1]
}
update_docker_node_extrahosts(server, project, dns["node_id"], extrahosts_dict_to_string(EXTRA_HOSTS))

check_ipaddrs(server, project)