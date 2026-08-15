# Rede da tese: selecção automática de protocolos (PQC estático vs. adaptativo)

Esta pasta contém os artefactos específicos da experiência da dissertação:
não faz parte do testbed Gotham original, mas reutiliza a infraestrutura
(`gns3utils.py`, template VyOS/Open vSwitch, padrão do `Makefile`) já
existente no projecto.

## Estado actual

1. **Rede** (`create_topology_tese.py` / `router_pqc.sh`) — feito.
2. **Cipher suite estático PQC** (`Dockerfiles/pqc_static/`,
   `create_templates_tese.py`, `run_benchmark_tese.py`) — feito, é este
   documento que o descreve.
3. **Modelo de selecção adaptativa** — próximo passo, ainda não
   implementado. Vai ser medido com o mesmo `run_benchmark_tese.py` (ou
   uma variante dele), sobre a mesma rede, para que os números sejam
   comparáveis com a baseline estática produzida aqui.

## Topologia

```
                        pqc-router (VyOS)
                     eth0 |        | eth1        eth2 (livre)
                          |        |
              pqc-switch-client   pqc-switch-server
                          |        |
                     pqc-client   pqc-server
```

Sem alterações desde o passo anterior — ver secção "Endereçamento" mais
abaixo.

## O cipher suite estático

`pqc-client` e `pqc-server` correm a mesma imagem Docker,
`iotsim/pqc-static`, construída em `Dockerfiles/pqc_static/Dockerfile`
sobre a build oficial do Open Quantum Safe (`openquantumsafe/oqs-ossl3`:
OpenSSL/master + `oqs-provider`, https://github.com/open-quantum-safe/oqs-provider).
O papel (cliente ou servidor) é decidido pela variável de ambiente `ROLE`,
definida por nó em `create_topology_tese.py` — não são duas imagens
diferentes.

- **KEM (troca de chaves)**: `mlkem768` — ML-KEM-768 (FIPS 203), categoria
  de segurança NIST 3.
- **Assinatura (certificados)**: `mldsa65` — ML-DSA-65 (FIPS 204), também
  categoria 3.
- Ambos **puros** (não híbridos com curva clássica), porque a comparação
  que a tese precisa é PQC estático vs. PQC adaptativo, não PQC vs.
  clássico.
- **Fixos em build-time** (`ARG PQC_KEM_GROUP` / `ARG PQC_SIG_ALG` no
  Dockerfile) — nada é negociado ou trocado em runtime. É isto que torna a
  suite "estática": o mesmo grupo é imposto tanto no `s_server` como em
  cada `s_client` (via `-groups`), pelo que não há margem para deriva
  entre execuções.
- O certificado (CA + servidor) também é gerado em build-time, pela mesma
  razão: o custo de gerar chaves/certificados não deve contaminar a
  medição de desempenho do handshake feita depois, em runtime.

Para testar outra suite estática (ex. uma variante híbrida
`x25519_mlkem768`, ou um nível de segurança diferente), reconstruir a
imagem com:
```
docker build --build-arg PQC_KEM_GROUP=x25519_mlkem768 --build-arg PQC_SIG_ALG=mldsa87 \
    -t iotsim/pqc-static Dockerfiles/pqc_static
```
e voltar a correr `create_templates_tese.py` (idempotente) +
`create_topology_tese.py` (recria o projecto GNS3, ver nota abaixo).

## Como correr

```
$ make pqc_static                        # constrói iotsim/pqc-static (na raiz do projecto)
$ cd src
(venv) $ python3 create_templates_tese.py   # regista o template no GNS3
(venv) $ python3 create_topology_tese.py    # cria a topologia (só da 1ª vez; falha se o projecto já existir)
(venv) $ python3 run_scenario_tese.py       # arranca router, switches, cliente, servidor
(venv) $ python3 run_benchmark_tese.py      # mede N handshakes TLS 1.3, grava CSV em ../tese/results/
```

`run_benchmark_tese.py` aceita `--iterations` (default 50) e `--warmup`
(default 3, descartadas antes de medir). Cada execução escreve um CSV
novo em `tese/results/static_<kem>_<sig>_<timestamp>.csv` com uma linha
por handshake (`iteration,ok,kem_group,sig_alg,handshake_time_s`).

## Porque um só par cliente/servidor (e não dois pares, um por variante)?

Para comparar PQC estático vs. adaptativo de forma válida, o ideal é manter
as condições de rede constantes entre as duas medições e variar apenas a
configuração criptográfica usada pelo cliente/servidor — não duplicar nós
em duas subredes separadas, que introduziria variáveis de confundimento
(ex. contenção de switch diferente, caminhos diferentes). Por isso a
topologia é deliberadamente mínima: um par cliente/servidor, medido em
duas execuções separadas (uma por variante) sobre a mesma rede.

## Como o handshake é medido (e uma limitação a citar na tese)

Cada handshake é um `openssl s_client ... -groups mlkem768 -quiet` corrido
dentro do container `pqc-client` via `docker exec` (Python `docker` SDK,
já usado no resto do projecto em `run_scenario_gotham.py`), cronometrado
no *host* com `time.perf_counter()` — não dentro do shell do container.

Isto foi deliberado: `/bin/sh` da imagem base não é garantidamente bash
(pode ser `dash` ou `ash`/busybox), e confirmei neste ambiente que a
palavra reservada `time` do bash **não está disponível em `dash`**
(`dash: 1: time: not found`), pelo que cronometrar dentro do container de
forma portátil não é fiável o suficiente para uma medição de tese.

**Limitação a documentar na dissertação**: cada valor medido inclui o
overhead de arrancar o processo via `docker exec`, além do handshake TLS
em si — não é o tempo puro de rede. Como o modelo adaptativo vai ser
medido exactamente da mesma forma, este overhead aplica-se de forma
consistente às duas condições, mas convém não citar estes números como
"tempo de handshake TLS puro" sem esta ressalva.

## Ficheiro de estado: `topology_state.json`

Gerado por `create_topology_tese.py` (não incluído no repositório),
mapeia `papel -> node_id` de cada nó, os endereços de cada zona e a porta
do servidor. `run_scenario_tese.py` e `run_benchmark_tese.py` leem este
ficheiro em vez de adivinhar pelos nomes por omissão do GNS3 (não fiáveis
quando há vários nós do mesmo template — ver comentário no próprio
`create_topology_tese.py`).

## Endereçamento

| Zona     | Rede               | Gateway (router) | Nó fixo             |
|----------|---------------------|-------------------|----------------------|
| Cliente  | 192.168.101.0/24    | 192.168.101.1     | pqc-client: .101.10  |
| Servidor | 192.168.102.0/24    | 192.168.102.1     | pqc-server: .102.10  |

Porta do servidor TLS: 4433 (constante `SERVER_PORT`, guardada em
`topology_state.json`).

## Próximos passos (fora do âmbito deste ficheiro)

1. Implementar o modelo de selecção adaptativa e a imagem/lógica
   correspondente (provavelmente uma variante de
   `Dockerfiles/pqc_static/`, com o grupo/algoritmo escolhido em runtime
   em vez de fixo em build-time).
2. Medir essa variante com o mesmo método (`run_benchmark_tese.py` ou
   adaptação directa dele), sobre a mesma topologia.
3. Comparar os CSVs de `tese/results/` (estático vs. adaptativo) —
   handshake time é o que já temos; se a dissertação quiser também CPU,
   memória ou tamanho de tráfego, isso implica estender o benchmark
   (`docker stats` / captura de pacotes já disponível nos switches, ver
   `START_CAPTURE` em `run_scenario_tese.py`).
4. Opcional: activar a `traffic-policy` comentada em `router_pqc.sh` para
   testar sob diferentes condições de rede (latência/perda/largura de
   banda), que é onde a vantagem da selecção *adaptativa* deve tornar-se
   visível face à estática.
