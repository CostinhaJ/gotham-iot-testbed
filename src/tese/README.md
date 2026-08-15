# Rede da tese: selecção automática de protocolos (PQC estático vs. adaptativo)

Esta pasta contém os artefactos específicos da experiência da dissertação:
não faz parte do testbed Gotham original, mas reutiliza a infraestrutura
(`gns3utils.py`, template VyOS/Open vSwitch, padrão do `Makefile`) já
existente no projecto.

> **Nota de reorganização**: as duas suites (estática e adaptativa) viviam
> antes em pastas separadas (`tese-pqc-gns3-static-suite/`,
> `tese-pqc-gns3-adaptive-suite/`); os seus scripts foram entretanto
> consolidados numa só pasta, `src/tese/` (esta pasta), com
> `Dockerfiles/pqc_static/` e `router/network_profiles/` movidos para a
> raiz do projecto. O `router_pqc.sh` original da suite estática não foi
> transportado; `router/network_profiles/clean.sh` já tinha o mesmo
> endereçamento base ("mesma config de router" — ver secção da suite
> adaptativa) e passou a ser usado como config inicial do router por
> ambas as suites.

## Estado actual

1. **Rede** (`create_topology_tese.py` / `router/network_profiles/clean.sh`) — feito.
2. **Cipher suite estático PQC** (`Dockerfiles/pqc_static/`,
   `create_templates_tese.py`, `run_benchmark_tese.py`) — feito, é este
   documento que o descreve.
3. **Modelo de rotação de chaves adaptativo** — infraestrutura de recolha
   de dados feita (`create_topology_adaptive_tese.py`,
   `run_experiment_tese.py`, ver secção própria abaixo); o middleware de
   decisão em si ainda não está implementado.

## Topologia

Comum às duas suites (cada uma no seu próprio projecto GNS3, ver secção
"Porque um projeto GNS3 separado" mais abaixo):

```
                        pqc-router (VyOS)
                     eth0 |        | eth1        eth2 (livre)
                          |        |
              pqc-switch-client   pqc-switch-server
                          |        |
                     pqc-client   pqc-server
```

## Endereçamento

| Zona     | Rede               | Gateway (router) | Nó fixo             |
|----------|---------------------|-------------------|----------------------|
| Cliente  | 192.168.101.0/24    | 192.168.101.1     | pqc-client: .101.10  |
| Servidor | 192.168.102.0/24    | 192.168.102.1     | pqc-server: .102.10  |

Porta do servidor TLS: 4433 (constante `SERVER_PORT`, guardada em
`topology_state.json`).

---

## Suite estática (baseline)

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

### Como correr

```
$ make pqc_static                           # constrói iotsim/pqc-static (na raiz do projecto)
$ cd src/tese
(venv) $ python3 create_templates_tese.py   # regista o template no GNS3
(venv) $ python3 create_topology_tese.py    # cria a topologia (só da 1ª vez; falha se o projecto já existir)
(venv) $ python3 run_scenario_tese.py       # arranca router, switches, cliente, servidor
(venv) $ python3 run_benchmark_tese.py      # mede N handshakes TLS 1.3, grava CSV em results/
```

`run_benchmark_tese.py` aceita `--iterations` (default 50) e `--warmup`
(default 3, descartadas antes de medir). Cada execução escreve um CSV
novo em `results/static_<kem>_<sig>_<timestamp>.csv` com uma linha
por handshake (`iteration,ok,kem_group,sig_alg,handshake_time_s`).

### Porque um só par cliente/servidor (e não dois pares, um por variante)?

Para comparar PQC estático vs. adaptativo de forma válida, o ideal é manter
as condições de rede constantes entre as duas medições e variar apenas a
configuração criptográfica usada pelo cliente/servidor — não duplicar nós
em duas subredes separadas, que introduziria variáveis de confundimento
(ex. contenção de switch diferente, caminhos diferentes). Por isso a
topologia é deliberadamente mínima: um par cliente/servidor, medido em
duas execuções separadas (uma por variante) sobre a mesma rede.

### Como o handshake é medido (e uma limitação a citar na tese)

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
em si — não é o tempo puro de rede. Como o modelo adaptativo é medido
exactamente da mesma forma, este overhead aplica-se de forma consistente
às duas condições, mas convém não citar estes números como "tempo de
handshake TLS puro" sem esta ressalva.

### Ficheiro de estado: `topology_state.json`

Gerado por `create_topology_tese.py` (não incluído no repositório, ver
`.gitignore`), mapeia `papel -> node_id` de cada nó, os endereços de cada
zona e a porta do servidor. `run_scenario_tese.py` e
`run_benchmark_tese.py` leem este ficheiro em vez de adivinhar pelos
nomes por omissão do GNS3 (não fiáveis quando há vários nós do mesmo
template — ver comentário no próprio `create_topology_tese.py`).

---

## Suite adaptativa: frequência de rotação de chaves PQC + telemetria rica

Cobre a **infraestrutura de recolha de dados** para o futuro middleware de
decisão — não o middleware em si.

### Correcção de design (importante, ler primeiro)

Uma primeira versão desta suite fazia "adaptativo" significar **trocar o
algoritmo** (KEM/assinatura) usado pelos endpoints, por trial, via
`docker update`/restart do container e um par de certificados por
algoritmo. Isso reintroduzia exactamente o overhead que uma abordagem
adaptativa devia evitar (restart de containers, certificados diferentes
por algoritmo) e confundia duas perguntas independentes.

O verdadeiro objectivo é outro: **dado um algoritmo fixo, com que
frequência deve o dispositivo refazer o handshake para rodar a chave?**
Em TLS 1.3 não há forma de repetir a operação KEM/assinatura sem um
handshake completo novo — o `KeyUpdate` nativo do TLS 1.3 só roda chaves
simétricas de tráfego, nunca volta a invocar o KEM nem a assinatura. Ou
seja, "rodar a chave" e "fazer outro handshake completo" são a mesma
operação aqui.

Consequência prática: **esta suite não tem imagem Docker própria**.
Reutiliza `iotsim-pqc-static`/`iotsim/pqc-static`
(`Dockerfiles/pqc_static/`, na raiz do projecto) sem alterações — um
algoritmo fixo, gerado em build-time, é exactamente o que esta
experiência também quer. O algoritmo (KEM+assinatura) é **constante para
toda a execução**, não é um eixo do sweep.

### Estado deste passo

1. **Frequência de rotação de chaves como variável independente do
   protocolo** — o único mecanismo "adaptativo" novo é um temporizador no
   lado do controlador (`sleep(rotation_interval_s)` entre handshakes),
   sem qualquer restart de container nem troca de certificados.
2. **Variabilidade de rede e de dispositivo** — 4 perfis de condição de
   rede (router VyOS, `traffic-policy network-emulator`) e 3 perfis de
   capacidade do dispositivo (`docker update --cpus/--memory`). Estes são
   genuinamente externos ao dispositivo (não são "a suite a mudar-se a si
   própria"), por isso são eixos do sweep.
3. **Telemetria rica por trial** — tempo de handshake, CPU/RAM/rede do
   dispositivo, e métricas ao nível de pacote (bytes totais, tamanho da
   mensagem Certificate, retransmissões TCP), tudo num único dataset CSV
   pronto para ML.

### Porque um projeto GNS3 separado da suite estática

`create_topology_tese.py` (suite estática) e `create_topology_adaptive_tese.py`
(esta suite) criam **dois projetos GNS3 diferentes** (`tese_pqc` vs.
`tese_pqc_adaptive`), embora usem a mesma imagem. A separação não é por
causa da imagem (é a mesma) — é para isolar o router com *traffic-shaping*
variável e os containers com CPU/RAM limitados desta suite da medição de
baseline "limpa" da suite estática, evitando que as condições de uma
contaminem os números da outra. Mesma topologia, mesmo endereçamento,
mesma configuração base do router (`router/network_profiles/clean.sh`,
partilhada pelas duas suites) — só isso muda.

### A variável de rotação de chaves

`run_experiment_tese.py` lê `PQC_KEM_GROUP`/`PQC_SIG_ALG` uma única vez,
do ambiente real do container `pqc-client` (tal como o benchmark da
suite estática já fazia), e usa-os em **todos** os handshakes da
execução — nunca muda. Para cada combinação (intervalo de rotação ×
perfil de rede × perfil de dispositivo), o cliente:

1. Faz um handshake de warmup (descartado).
2. Espera `rotation_interval_s` segundos, faz um handshake, regista.
3. Repete o passo 2 até `observation_window_s` segundos terem passado
   (pelo menos uma rotação acontece sempre, mesmo que o intervalo seja
   maior que a janela — esse é também um ponto de dados válido: "quanto
   custa/poupa nunca rodar durante esta janela").

O número de handshakes por trial **varia com o intervalo** — um
intervalo de 5s numa janela de 5 minutos dá ~60 rotações; um intervalo
de 300s dá 1. É isso que torna `trial_rotation_count` e as métricas de
recursos agregadas úteis em conjunto: o custo total ao longo de uma
janela de operação realista, em função do intervalo — exactamente o que
um middleware futuro teria de equilibrar contra o tempo de exposição da
chave (rodar menos = mais barato, mas a chave fica exposta mais tempo).

### Perfis de condição de rede

`clean` (sem shaping, é também a config base do router), `constrained`
(tipo LPWAN: ~250kbit, ~200ms), `lossy` (~50ms, 5-10% perda),
`high_latency` (tipo satélite/celular: ~600ms, ~1mbit), aplicados em
`eth1` do router. Scripts em `router/network_profiles/*.sh`.

**Aviso de sintaxe não verificada**: os scripts em `router/network_profiles/*.sh`
usam `traffic-policy network-emulator` (o wrapper netem do VyOS) —
sintaxe **não validada contra uma instância VyOS 1.3.0 real**, primeiro
passo do smoke test abaixo.

Trocar de perfil de rede continua caro (reiniciar o router duas vezes,
~2 min) — continua a ser o **loop mais externo** do sweep.

### Perfis de capacidade do dispositivo

`constrained` (0.2 CPU, 128MB), `typical` (0.5 CPU, 256MB),
`unconstrained` (sem limite), via `docker update`, sem restart — **loop
do meio**.

### Intervalos de rotação por omissão

`5, 15, 30, 60, 120, 300` segundos, com janela de observação de 300s por
omissão (o intervalo de 300s dá, por isso, exactamente 1 rotação — o
ponto de referência "praticamente sem rotação"). Ambos configuráveis via
`--rotation-intervals` e `--observation-window-s`. Este é o **loop mais
interno** (mais barato que tudo o resto: mudar o intervalo é só mudar um
número no controlador, sem tocar em containers).

### Telemetria e esquema do dataset

Uma linha **por rotação** (não por média de trial): um middleware de
decisão futuro vai raciocinar por evento de rotação, não por agregado —
colapsar para média deitaria fora a variância entre rotações.

Saída: `results/adaptive_sweep_<timestamp>.csv`, um ficheiro por
execução completa de `run_experiment_tese.py`.

| coluna | grão | notas |
|---|---|---|
| `run_id`, `trial_id`, `suite_type` | execução/trial | `suite_type` é sempre `adaptive_rotation` neste script |
| `kem_group`, `sig_alg` | execução | **constante** em toda a execução — logado por linha só para rastreabilidade, não varia entre trials |
| `rotation_interval_s`, `observation_window_s` | trial | os dois parâmetros que definem o trial |
| `network_profile`, `network_delay_ms`, `network_loss_pct`, `network_bandwidth_kbit` | trial | perfil de rede aplicado |
| `device_profile`, `device_cpu_limit`, `device_mem_limit_mb` | trial | perfil de dispositivo aplicado |
| `rotation_index`, `ok`, `handshake_time_s` | rotação | mesmo método de cronometragem da suite estática (ver limitação acima) |
| `trial_rotation_count` | trial (repetido) | quantas rotações couberam na janela — varia com `rotation_interval_s` |
| `trial_cpu_pct_mean/max`, `trial_mem_mb_mean/max` | trial (repetido) | amostrado continuamente durante o trial |
| `trial_net_rx_bytes`, `trial_net_tx_bytes` | trial (repetido) | **delta** de contadores cumulativos entre início e fim do trial |
| `trial_pcap_total_bytes`, `trial_clienthello_bytes`, `trial_serverhello_bytes`, `trial_certificate_bytes`, `trial_tcp_retransmits`, `trial_capture_duration_s` | trial (repetido) | ver "Nível de pacote e TLS 1.3" abaixo |
| `timestamp_utc` | trial | início do trial |

#### Nível de recursos (CPU/RAM/rede)

`docker stats` tem granularidade ~1s. CPU%/memória são amostrados
continuamente ao longo de toda a janela de observação do trial e
atribuídos **ao nível do trial**, nunca por rotação individual.

#### Nível de pacote e TLS 1.3

Como o algoritmo é fixo para toda a execução, o tamanho da mensagem
Certificate **não varia entre trials** — o que varia é quantas vezes por
trial esse custo é pago (`trial_rotation_count`). `trial_pcap_total_bytes`
escalado por `trial_rotation_count` é o custo de largura de banda de uma
dada frequência de rotação, que é precisamente o que interessa aqui. Como
em TLS 1.3 tudo depois do ServerHello vai cifrado com as chaves de
handshake, uma captura passiva não vê o tamanho da mensagem Certificate
sem decifrar — por isso o comando de handshake usa
`openssl s_client -keylogfile`, e o keylog acumulado ao longo do trial é
descarregado e passado ao `tshark`/`pyshark` para decifrar antes do
parsing (ver `capture_utils.py`, nesta mesma pasta). Sem isto,
`trial_certificate_bytes` seria sempre 0 — confirmar no smoke test que a
decifragem está mesmo a funcionar antes de confiar nestes números.

### Como correr

```
$ make pqc_static                                   # constrói iotsim/pqc-static (na raiz do projeto; partilhado com a suite estática)
$ cd src/tese
(venv) $ python3 create_templates_tese.py           # regista o template (se ainda não estiver registado)
(venv) $ python3 create_topology_adaptive_tese.py   # cria a topologia (só da 1ª vez)
(venv) $ python3 run_scenario_adaptive_tese.py      # arranca router, switches, cliente, servidor
(venv) $ python3 run_experiment_tese.py             # sweep completo, grava CSV em results/
(venv) $ python3 teardown_tese.py                   # limpa: pára captura, repõe router 'clean', pára os 5 nós
```

`run_experiment_tese.py` aceita `--rotation-intervals` (lista de
segundos, default `5 15 30 60 120 300`), `--observation-window-s`
(default 300), `--network-profiles`/`--device-profiles` (subconjuntos,
default todos) e `--output` (caminho do CSV). Não tem `--iterations`,
`--warmup` nem `--full-cross` — esses conceitos pertenciam ao design
anterior (troca de algoritmo) e já não se aplicam.

Requer `pyshark` (ver `requirements.txt`, na raiz do projecto) **e** o
binário `tshark` (Wireshark CLI) instalado no host que corre o sweep.

### Verificação antes de confiar num sweep completo

Não automatizável nesta fase (precisa de um servidor GNS3 real):

1. Confirmar a sintaxe `traffic-policy network-emulator` numa VyOS 1.3.0
   real antes de usar os perfis de rede num sweep.
2. Confirmar que `configure_vyos_image_on_node` (com o fix `>>`→`>` em
   `src/gns3utils.py`, na raiz do projecto) aceita duas reconfigurações
   seguidas sem corromper o ficheiro.
3. Confirmar que `iotsim/pqc-static` já está construído (`make
   pqc_static` na raiz) e o template `iotsim-pqc-static` já está
   registado (`create_templates_tese.py`) — esta suite depende dos dois.
4. Testar `start_capture`/`stop_capture` + `download_capture_file` e
   confirmar que os pcaps obtidos abrem no Wireshark/tshark.
5. Smoke test em pequena escala: `--rotation-intervals 10
   --observation-window-s 30 --network-profiles clean
   --device-profiles unconstrained` — confirmar linhas do CSV (deve dar
   ~2-3 rotações), `trial_certificate_bytes` não nulo (confirma que a
   decifragem TLS 1.3 via keylog está a funcionar), e que
   `teardown_tese.py` deixa mesmo tudo parado e sem shaping.
6. Só depois correr a matriz completa por omissão (6 intervalos × 4
   perfis de rede × 3 de dispositivo = 72 trials, cada um até 300s de
   janela — o sweep completo pode demorar várias horas, sobretudo pelas
   reconfigurações do router; considerar `--network-profiles`/
   `--device-profiles` reduzidos numa primeira passagem).

---

## Próximos passos (fora do âmbito destes ficheiros)

1. O middleware de decisão em si — treinar/avaliar um modelo (regras ou
   ML) que escolha o intervalo de rotação em runtime a partir das
   condições observadas — consome o CSV produzido por
   `run_experiment_tese.py` como dataset de treino/avaliação, mas não
   está implementado nesta pasta.
2. Comparar os CSVs de `results/` (estático vs. adaptativo) — handshake
   time é o que já temos; se a dissertação quiser também CPU, memória ou
   tamanho de tráfego, isso implica estender o benchmark estático
   (`docker stats` / captura de pacotes já disponível nos switches, ver
   `START_CAPTURE` em `run_scenario_tese.py`).
3. Se a tese vier a querer também comparar entre diferentes algoritmos
   fixos (ex: correr esta mesma varredura de intervalos uma vez com
   ML-KEM-768/ML-DSA-65 e outra com uma baseline clássica), isso é uma
   execução separada e independente (reconstruir `iotsim/pqc-static` com
   outro `--build-arg` e repetir `create_topology_adaptive_tese.py` +
   `run_experiment_tese.py`), não um eixo dentro do mesmo sweep —
   mantendo a variável de rotação isolada da variável de algoritmo.
