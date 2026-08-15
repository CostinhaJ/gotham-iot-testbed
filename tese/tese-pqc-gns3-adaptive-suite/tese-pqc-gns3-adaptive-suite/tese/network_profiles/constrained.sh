#!/bin/vbash
# Network profile "constrained": LPWAN-like link on the server-zone side
# (eth1) -- low bandwidth, moderate delay. See clean.sh for why this is a
# full config (not a delta) and for the base interfaces/hostname block.
#
# UNVERIFIED SYNTAX WARNING: `traffic-policy network-emulator` (VyOS's
# netem wrapper, needed for delay -- `traffic-policy shaper` only covers
# bandwidth/queueing) has not been exercised against a live VyOS 1.3.0
# instance as part of this change. Confirm/adjust these `set` commands by
# hand on a freshly-installed router BEFORE wiring this into
# run_experiment_tese.py's sweep -- see ../README.md, smoke-test step 1.

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

set traffic-policy network-emulator ADAPTIVE-NETEM bandwidth '250kbit'
set traffic-policy network-emulator ADAPTIVE-NETEM network-delay '200'
set interfaces ethernet eth1 traffic-policy out 'ADAPTIVE-NETEM'

commit
save

exit
