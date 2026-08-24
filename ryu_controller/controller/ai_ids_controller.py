"""
ai_ids_controller.py
---------------------
Controlador RYU (OpenFlow 1.3) que funciona como:

  1. Learning switch L2 basico (aprende MAC -> porta e instala flows para
     evitar packet-ins constantes).
  2. Monitor periodico que pede estatisticas de fluxo (OFPFlowStatsRequest)
     a cada switch ligado.
  3. Para cada fluxo ativo, extrai um vetor de features e entrega-o a um
     classificador de IA (ml/classifier.py).
  4. Se o classificador marcar o fluxo como malicioso, instala uma flow
     rule de prioridade alta que faz DROP desse trafego (sem "actions").

Como correr:
    ryu-manager --ofp-tcp-listen-port 6653 ai_ids_controller.py

Os switches (Open vSwitch) na topologia GNS3 devem apontar para o IP da
maquina onde este controlador corre, na porta 6653, com protocolo
OpenFlow13 (ver gns3/topology_notes.md para os comandos ovs-vsctl).
"""

import sys
import os

# Permite importar ml/classifier.py independentemente de onde o ryu-manager
# for chamado.
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.lib import hub

from ml.classifier import TrafficClassifier, LABEL_MALICIOUS

# ---------------------------------------------------------------------------
# Parametros ajustaveis
# ---------------------------------------------------------------------------
MONITOR_INTERVAL_SEC = 10       # intervalo entre pedidos de estatisticas
BLOCK_PRIORITY = 100            # prioridade da flow rule de bloqueio
BLOCK_HARD_TIMEOUT = 120        # segundos ate a regra de bloqueio expirar (0 = permanente)
MIN_PACKETS_TO_CLASSIFY = 5     # ignora fluxos com poucos pacotes (ruido/arranque)


class AiIdsSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(AiIdsSwitch, self).__init__(*args, **kwargs)

        # MAC learning table: {dpid: {mac: porta}}
        self.mac_to_port = {}

        # Switches (datapaths) atualmente ligados: {dpid: datapath}
        self.datapaths = {}

        # Fluxos ja bloqueados, para nao voltar a instalar a mesma regra
        # repetidamente: {dpid: set of match_tuple}
        self.blocked_flows = {}

        self.classifier = TrafficClassifier()
        self.monitor_thread = hub.spawn(self._monitor)

    # ------------------------------------------------------------------
    # Gestao de ligacao dos switches
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.info("Switch ligado: dpid=%s", datapath.id)
                self.datapaths[datapath.id] = datapath
                self.blocked_flows.setdefault(datapath.id, set())
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.info("Switch desligado: dpid=%s", datapath.id)
                del self.datapaths[datapath.id]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Table-miss: qualquer pacote sem match vai para o controlador.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None,
                 idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(
                datapath=datapath, buffer_id=buffer_id, priority=priority,
                match=match, instructions=inst,
                idle_timeout=idle_timeout, hard_timeout=hard_timeout,
            )
        else:
            mod = parser.OFPFlowMod(
                datapath=datapath, priority=priority,
                match=match, instructions=inst,
                idle_timeout=idle_timeout, hard_timeout=hard_timeout,
            )
        datapath.send_msg(mod)

    # ------------------------------------------------------------------
    # Learning switch L2
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return  # ignora LLDP (usado para topology discovery)

        dst = eth.dst
        src = eth.src

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Instala flow para trafego futuro entre este par de MACs, evitando
        # novos packet-ins (e permitindo tambem que o monitor recolha
        # estatisticas reais deste fluxo).
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, 1, match, actions, msg.buffer_id, idle_timeout=60)
                return
            else:
                self.add_flow(datapath, 1, match, actions, idle_timeout=60)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
            actions=actions, data=data,
        )
        datapath.send_msg(out)

    # ------------------------------------------------------------------
    # Monitor periodico de estatisticas -> classificador de IA
    # ------------------------------------------------------------------
    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                self._request_flow_stats(dp)
            hub.sleep(MONITOR_INTERVAL_SEC)

    def _request_flow_stats(self, datapath):
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        datapath = ev.msg.datapath

        for stat in ev.msg.body:
            # Ignora a flow de table-miss (priority 0, sem match especifico)
            # e a flow de learning switch (priority 1), foca-se em fluxos
            # com trafego real ja acumulado.
            if stat.priority < 1:
                continue
            if stat.packet_count < MIN_PACKETS_TO_CLASSIFY:
                continue

            features, match_key = self._extract_features(stat)
            label, score = self.classifier.predict(features)

            if label == LABEL_MALICIOUS:
                self._handle_malicious_flow(datapath, stat, match_key, score)

    @staticmethod
    def _extract_features(stat):
        """Converte um OFPFlowStats num dicionario de features (ver
        ml/classifier.py FEATURE_NAMES) e numa chave de match reutilizavel
        para instalar a flow rule de bloqueio.
        """
        duration_sec = max(stat.duration_sec + stat.duration_nsec / 1e9, 0.001)
        packet_count = stat.packet_count
        byte_count = stat.byte_count

        match = stat.match
        ip_proto = match.get("ip_proto", 0)
        src_port = match.get("tcp_src", match.get("udp_src", 0))
        dst_port = match.get("tcp_dst", match.get("udp_dst", 0))

        features = {
            "duration_sec": duration_sec,
            "packet_count": packet_count,
            "byte_count": byte_count,
            "avg_pkt_size": byte_count / packet_count if packet_count else 0,
            "pkt_rate": packet_count / duration_sec,
            "byte_rate": byte_count / duration_sec,
            "ip_proto": ip_proto,
            "src_port": src_port,
            "dst_port": dst_port,
        }

        # Chave usada so para deduplicar bloqueios (nao precisa de ser
        # exaustiva, so suficientemente especifica para o teu cenario).
        match_key = (
            match.get("in_port"),
            match.get("eth_src"),
            match.get("eth_dst"),
            match.get("ipv4_src"),
            match.get("ipv4_dst"),
            src_port,
            dst_port,
            ip_proto,
        )
        return features, match_key

    def _handle_malicious_flow(self, datapath, stat, match_key, score):
        dpid = datapath.id
        blocked = self.blocked_flows.setdefault(dpid, set())

        if match_key in blocked:
            return  # ja bloqueado, nao repete

        parser = datapath.ofproto_parser

        # Reconstroi um match a partir do match original do fluxo detetado,
        # para bloquear especificamente esse trafego (5-tuplo quando
        # disponivel).
        match_fields = {}
        for field in ("in_port", "eth_src", "eth_dst", "eth_type",
                      "ipv4_src", "ipv4_dst", "ip_proto",
                      "tcp_src", "tcp_dst", "udp_src", "udp_dst"):
            if field in stat.match:
                match_fields[field] = stat.match[field]

        match = parser.OFPMatch(**match_fields)

        # instructions=[] -> sem acoes = DROP
        self.add_flow(
            datapath, BLOCK_PRIORITY, match, actions=[],
            hard_timeout=BLOCK_HARD_TIMEOUT,
        )
        blocked.add(match_key)

        self.logger.warning(
            "[IA-IDS] Fluxo malicioso detetado em dpid=%s (score=%.2f) -> "
            "BLOQUEADO (match=%s)", dpid, score, match_fields,
        )
