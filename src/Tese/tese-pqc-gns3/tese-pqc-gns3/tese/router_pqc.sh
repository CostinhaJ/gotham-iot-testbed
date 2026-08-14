#!/bin/vbash
# Router de laboratorio para a experiencia da tese (PQC estatico vs. PQC adaptativo).
#
# Duas interfaces, uma por "zona":
#   eth0 -> zona cliente  (192.168.101.0/24), gateway 192.168.101.1
#   eth1 -> zona servidor (192.168.102.0/24), gateway 192.168.102.1
#   eth2 -> livre (reservado para extensoes futuras, ex.: no de monitorizacao)
#
# Sem rotas dinamicas nem rota por omissao: as duas redes ficam directamente
# ligadas neste router, o que chega para o cenario de "rede simples" pedido.
# O router garante que o trafego cliente<->servidor passa sempre por um salto
# de nivel 3, tal como aconteceria numa ligacao real entre um dispositivo IoT
# e um servidor/broker.
#
# Ponto de extensao para trabalho futuro (FORA do ambito deste passo):
# para simular condicoes de rede variaveis (latencia, perda de pacotes,
# largura de banda) entre as duas zonas, e assim ter cenarios onde faca
# sentido comparar selecao estatica vs. adaptativa de protocolos PQC,
# pode aplicar-se aqui uma traffic-policy por interface, por exemplo:
#
#   set traffic-policy shaper WAN-LIMITADA bandwidth '10mbit'
#   set traffic-policy shaper WAN-LIMITADA default bandwidth '10mbit'
#   set traffic-policy shaper WAN-LIMITADA default queue-type 'fair-queue'
#   set interfaces ethernet eth1 traffic-policy out 'WAN-LIMITADA'
#
# Nao e activado aqui para nao acoplar a topologia de base a um cenario de
# rede especifico; a decisao de que perfis de rede testar deve acompanhar a
# implementacao do modelo (proximo passo).

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 address '192.168.101.1/24'
set interfaces ethernet eth1 address '192.168.102.1/24'
set interfaces loopback lo
set system config-management commit-revisions '100'
set system console device ttyS0 speed '115200'
set system host-name 'Rpqc'
set system login user vyos authentication encrypted-password '$6$6N6NsVkVe3sd1BL$h/ExSfPoFCLVxxdzLTOLuL2O.qJxMTfQflnrcEXOSTQBVgx5tWXci8PNhgQP5fp8x7UwEfMduOzxQj4eh4BQ3/'
set system login user vyos authentication plaintext-password ''
set system ntp server 0.pool.ntp.org
set system ntp server 1.pool.ntp.org
set system ntp server 2.pool.ntp.org
set system syslog global facility all level 'info'
set system syslog global facility protocols level 'debug'

commit
save

exit
