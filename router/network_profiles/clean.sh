#!/bin/vbash
# Network profile "clean": no shaping -- the router's baseline config.
#
# Doubles as BOTH the router's initial configuration (see
# ../../src/tese/create_topology_adaptive_tese.py, ROUTER_CONFIG_SCRIPT, and
# ../../src/tese/create_topology_tese.py, which uses the same script for the
# static suite) and a selectable profile during the sweep (see
# ../../src/tese/network_profiles.py) to reset the router between trials
# that need no shaping applied.
#
# Every profile script in this directory is a FULL config, not a delta:
# VyOS persists configuration across reboots with `save`, so each script
# must redeclare the base interfaces/hostname too, or a later profile
# switch would silently lose them. Keep constrained.sh / lossy.sh /
# high_latency.sh in sync with the base block below when editing it.
#
# Two interfaces, one per "zone" (same addressing used by both the static
# and adaptive suites, for comparability):
#   eth0 -> zona cliente  (192.168.101.0/24), gateway 192.168.101.1
#   eth1 -> zona servidor (192.168.102.0/24), gateway 192.168.102.1
#   eth2 -> livre (reservado)
#
# No dynamic routing / no default route: the two zones are directly
# connected on this router, forcing client<->server traffic through one
# L3 hop, as in a real IoT device <-> server/broker path.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 address '192.168.101.1/24'
set interfaces ethernet eth1 address '192.168.102.1/24'
set interfaces loopback lo
set system config-management commit-revisions '100'
set system console device ttyS0 speed '115200'
set system host-name 'Rpqcadaptive'
set system login user vyos authentication encrypted-password '$6$6N6NsVkVe3sd1BL$h/ExSfPoFCLVxxdzLTOLuL2O.qJxMTfQflnrcEXOSTQBVgx5tWXci8PNhgQP5fp8x7UwEfMduOzxQj4eh4BQ3/'
set system login user vyos authentication plaintext-password ''
set system ntp server 0.pool.ntp.org
set system ntp server 1.pool.ntp.org
set system ntp server 2.pool.ntp.org
set system syslog global facility all level 'info'
set system syslog global facility protocols level 'debug'

# No traffic-policy applied in this profile -- if the router already has
# ADAPTIVE-NETEM configured from a previous profile in the same session,
# explicitly delete it so "clean" really means clean regardless of
# history. delete on a nonexistent path is a no-op in VyOS, safe to run
# unconditionally.

delete interfaces ethernet eth1 traffic-policy
delete traffic-policy network-emulator ADAPTIVE-NETEM

commit
save

exit
