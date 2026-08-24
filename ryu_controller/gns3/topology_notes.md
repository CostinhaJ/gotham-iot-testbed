# Ligar o RYU a topologia GNS3

Assumindo que ja tens a topologia montada (switches + hosts) e so falta
ligar o controlador, este ficheiro cobre as duas formas mais comuns de o
fazer.

## Opcao A -- RYU a correr no host, fora do GNS3 (recomendado)

1. No GNS3, adiciona um no **Cloud** (nuvem) ligado a mesma rede/bridge
   que os teus switches OpenFlow, OU garante que a rede do GNS3 (modo
   local server) e acessivel a partir do teu host pelo adaptador de loopback
   do GNS3 (normalmente `vmnet`/`Ethernet` "GNS3 VM" ou `127.0.0.1` se
   correres tudo local sem VM).
2. Descobre o IP que os switches devem usar para alcancar o teu host
   (por exemplo `192.168.122.1` ou o IP do adaptador loopback do GNS3).
3. Em cada switch **Open vSwitch** da topologia, configura o controlador:

   ```bash
   ovs-vsctl set-controller br0 tcp:<IP_DO_HOST>:6653
   ovs-vsctl set bridge br0 protocols=OpenFlow13
   ovs-vsctl set-fail-mode br0 secure
   ```

   (substitui `br0` pelo nome da bridge de cada switch na tua topologia).

4. No host, corre o controlador:

   ```bash
   ryu-manager --ofp-tcp-listen-port 6653 controller/ai_ids_controller.py
   ```

5. Confirma a ligacao:

   ```bash
   ovs-vsctl show          # deve mostrar "is_connected: true" no controller
   ovs-ofctl -O OpenFlow13 dump-flows br0
   ```

## Opcao B -- RYU dentro de um no GNS3 (Docker/VPCS Linux)

Se preferires manter tudo dentro da topologia (sem depender do host):

1. Usa um no Docker no GNS3 com uma imagem que tenha Python 3.8/3.9
   (ex: `python:3.9-slim`) e instala as dependencias de
   `requirements.txt` dentro dele.
2. Liga esse no a mesma rede dos switches.
3. Aponta os switches para o IP desse no de controlador (em vez do IP do
   host), com os mesmos comandos `ovs-vsctl` da Opcao A.
4. Corre o `ryu-manager` dentro do container/no.

## Coisas a validar antes de testar o modelo de IA

- Confirma que os switches estao mesmo em `OpenFlow13` (`ovs-vsctl get
  bridge br0 protocols`).
- Gera trafego entre hosts da topologia (ping, iperf, etc.) e confirma
  que aparecem flows com `ovs-ofctl dump-flows` -- isto é o que o
  controlador vai usar para extrair features.
- Para testares o bloqueio automatico sem esperar pelo modelo treinado, o
  classificador de fallback em `ml/classifier.py` ja marca como
  suspeito trafego com muitos pacotes/seg (ex: um `ping -f` ou um
  `hping3 --flood`), o que e util para validar a parte de "deteta ->
  instala flow de DROP" antes de teres o modelo real pronto.

## Troubleshooting comum do RYU

- `ImportError` relacionado com `eventlet`/`dns` -> normalmente e
  incompatibilidade de versoes; usa as versoes fixadas em
  `requirements.txt` dentro de um virtualenv dedicado.
- RYU so oficialmente testado ate Python 3.9 -- se tiveres Python 3.10+
  no host, considera um virtualenv com `pyenv` ou correr o controlador
  num no Docker com Python 3.9 (Opcao B).
- Se os switches nunca ficam "is_connected: true", confirma firewall
  local a bloquear a porta 6653 e confirma que o IP configurado no
  `ovs-vsctl set-controller` e mesmo alcancavel a partir do switch
  (testa com `ping`/`nc -zv <IP_DO_HOST> 6653` a partir do no do switch).
