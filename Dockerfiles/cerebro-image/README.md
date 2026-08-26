# Componente Cérebro — Prometheus + Grafana (imagem única)

## 0. Build via make (recomendado)
Se colocaste esta pasta em `Dockerfiles/cerebro/` dentro do projeto, basta:
```bash
make buildstatus/cerebro
```
a partir da raiz do projeto (onde está o `Makefile`). Isto já trata do `docker build`
com a tag correta (`iotsim/cerebro`) e regista o `buildstatus/cerebro` para não repetir
o build se nada tiver mudado. Os passos manuais abaixo continuam a funcionar,
mas deixam de ser necessários.

## 1. Antes de fazer build (build manual)
Edita `prometheus.yml` e substitui os IPs/portas de exemplo (marcados com `<-- EDITA`)
pelos alvos reais da tua topologia (cAdvisor, Telegraf no VyOS, sFlow-RT no OVS).

## 2. Build da imagem
No host onde tens o Docker (o mesmo que o GNS3 usa para os templates Docker):

```bash
cd cerebro-image
docker build -t iotsim/cerebro:latest .
```

## 3. Testar isoladamente (opcional, antes de meter no GNS3)
```bash
docker run -d --name cerebro-test -p 9090:9090 -p 3000:3000 iotsim/cerebro:latest
```
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin / admin123)

## 3.5 Ligação de rede ao host (para o cAdvisor)
O nó Cérebro precisa de alcançar o host físico (onde corre o cAdvisor). Forma recomendada:
1. Adiciona um nó **Cloud** no GNS3, associado à interface `docker0`
2. Liga esse Cloud à interface de gestão (`eth0`) do nó Cérebro
3. Dentro do container Cérebro, define um IP estático fora do range dinâmico do Docker,
   ex: `172.17.99.99/16`, gateway `172.17.0.1`
4. Confirma com: `curl 172.17.0.1:8080/metrics` (deve devolver métricas do cAdvisor)

## 3.6 Segunda interface de rede (para routers e OVS)
O Cérebro precisa de uma segunda interface, ligada à topologia GNS3 (não ao host),
para alcançar os routers VyOS e as pontes OVS.

1. No GNS3, liga um segundo adaptador do nó Cérebro à `OVS-6` (switch do Site A)
2. Dentro do container, configura um IP fixo nessa sub-rede:
   ```bash
   ip addr add 192.168.0.250/20 dev eth1
   ip route add default via 192.168.0.1 dev eth1 metric 200
   ```
   (usa `metric` mais alta que a rota por omissão da 1ª interface, para não
   competir com o caminho para o docker0/cAdvisor)
3. Testa a partir do Cérebro: `ping 192.168.16.1` (Router 2) — se responder,
   o routing entre sites já está a fazer o trabalho por ti.

## 4. Registar como template Docker no GNS3
No GNS3: **Edit > Preferences > Docker containers > New**
- Image: `iotsim/cerebro:latest`
- Adapters: 2 (eth0 -> Cloud/docker0 para o cAdvisor, eth1 -> OVS-6 para routers/OVS)
- Start command: deixa em branco (usa o ENTRYPOINT da imagem)
- Console type: `none` ou `http` na porta 3000 (para abrir o Grafana direto a partir do GNS3)

Depois arrasta o novo template para a topologia e liga-o à rede de gestão,
tal como fizeste com os outros nós.

## 5. Se mudares os IPs da topologia depois
Como o `prometheus.yml` está "cozido" dentro da imagem, qualquer alteração
de IPs implica repetir o passo 2 (rebuild). Se preferires não voltar a
fazer build sempre que a topologia mudar, diz-me — dá para adaptar o
Dockerfile para ler o `prometheus.yml` de um volume montado pelo GNS3
("extra files" do template Docker) em vez de o copiar para dentro da imagem.
