#!/bin/vbash
# Network profile "high_latency": satellite/cellular-like link on the
# server-zone side (eth1) -- high delay, moderate bandwidth cap, no
# induced loss (isolates delay's effect on handshake time from lossy.sh's
# loss effect). See clean.sh for why this is a full config (not a delta)
# and for the base interfaces/hostname block, and for the UNVERIFIED
# SYNTAX WARNING that applies to all `traffic-policy network-emulator`
# profiles here.

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

set traffic-policy network-emulator ADAPTIVE-NETEM network-delay '600'
set traffic-policy network-emulator ADAPTIVE-NETEM bandwidth '1mbit'
set interfaces ethernet eth1 traffic-policy out 'ADAPTIVE-NETEM'

commit
save

exit
