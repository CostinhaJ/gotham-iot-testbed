# Rede da tese: selecção automática de protocolos (PQC estático vs. adaptativo)

Esta pasta contém os artefactos específicos da experiência da dissertação:
não faz parte do testbed Gotham original, mas reutiliza a infraestrutura
(`gns3utils.py`, templates Docker, template VyOS/Open vSwitch) já existente
no projecto.

## O que este passo faz (e o que não faz)

Este passo cria **apenas a rede** no GNS3: routing, switching e dois nós
placeholder (cliente/servidor). **Não** instala nem executa nenhuma
criptografia PQC — isso fica para o próximo passo, quando o modelo de
selecção adaptativa for implementado e a imagem Docker com as bibliotecas
PQC (ex. liboqs / oqs-provider) substituir o template placeholder.

## Topologia

```
                        pqc-router (VyOS)
                     eth0 |        | eth1        eth2 (livre)
                          |        |
              pqc-switch-client   pqc-switch-server
                          |        |
                     pqc-client   pqc-server
```

- **pqc-router**: router VyOS com uma interface por zona. Não tem rota por
  omissão nem protocolos de encaminhamento dinâmico — as duas redes ficam
  directamente ligadas, o que é suficiente para o objectivo de "rede
  simples". Configurado a partir de `router_pqc.sh`.
- **pqc-switch-client / pqc-switch-server**: switches Open vSwitch, um por
  zona. Servem também como pontos de captura de pacotes (Wireshark via
  GNS3), úteis para medir mais tarde handshake time / throughput.
- **pqc-client / pqc-server**: neste passo são o template `iotsim-debug-client`
  (Alpine + ferramentas de rede), usado apenas como placeholder para validar
  a rede. **Quando o modelo estiver pronto para ser implementado**, criar um
  template Docker próprio (com as bibliotecas PQC) via `create_templates.py`
  / `Makefile`, e trocar a constante `ENDPOINT_TEMPLATE_NAME` em
  `create_topology_tese.py`.

## Porque um só par cliente/servidor (e não dois pares, um por variante)?

Para comparar PQC estático vs. adaptativo de forma válida, o ideal é manter
as condições de rede constantes entre as duas medições e variar apenas a
configuração criptográfica usada pelo cliente/servidor — não duplicar nós
em duas subredes separadas, que introduziria variáveis de confundimento
(ex. contenção de switch diferente, caminhos diferentes). Por isso a
topologia gerada aqui é deliberadamente mínima: um par cliente/servidor,
corrido duas vezes (uma por variante) sobre a mesma rede. Se, mais tarde,
precisar de correr as duas variantes em simultâneo, pode duplicar a "zona
servidor" reutilizando o mesmo padrão.

## Endereçamento

| Zona     | Rede               | Gateway (router) | Nó fixo             |
|----------|---------------------|-------------------|----------------------|
| Cliente  | 192.168.101.0/24    | 192.168.101.1     | pqc-client: .101.10  |
| Servidor | 192.168.102.0/24    | 192.168.102.1     | pqc-server: .102.10  |

## Ficheiro de estado: `topology_state.json`

`create_topology_tese.py` grava neste ficheiro (gerado, não incluído no
repositório) o mapeamento `papel -> node_id` de cada nó criado. O
`run_scenario_tese.py` lê este ficheiro para saber exactamente que nó
iniciar/capturar, em vez de adivinhar pelos nomes por omissão do GNS3 (a
atribuição de nomes automática do GNS3 não é fiável quando há vários nós
criados a partir do mesmo template, como é o caso de cliente/servidor aqui).

## Próximos passos (fora do âmbito deste ficheiro)

1. Construir a imagem Docker com o cliente/servidor PQC (estático e
   adaptativo) e registá-la como template GNS3.
2. Substituir `ENDPOINT_TEMPLATE_NAME` em `create_topology_tese.py`.
3. Implementar o script de benchmark (handshake time, CPU, throughput) que
   corre sobre `pqc-client` <-> `pqc-server`.
4. Opcional: activar a `traffic-policy` comentada em `router_pqc.sh` para
   testar sob diferentes condições de rede (latência/perda/largura de
   banda), o que é onde a vantagem da selecção *adaptativa* deve tornar-se
   visível face à selecção estática.
