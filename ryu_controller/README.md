# Controlador RYU com deteção de tráfego malicioso por IA (SDN + GNS3)

Controlador RYU (OpenFlow 1.3) que:

1. Funciona como *learning switch* L2 (aprende MACs e instala flows).
2. Pede estatísticas de fluxo periodicamente a cada switch (`OFPFlowStatsRequest`).
3. Extrai features de cada fluxo ativo e entrega-as a um classificador de IA.
4. Se o classificador marcar o fluxo como malicioso, instala uma flow rule
   de alta prioridade que faz **drop** desse tráfego automaticamente.

## Estrutura

```
ryu-sdn-ids/
├── controller/
│   └── ai_ids_controller.py   <- app RYU principal
├── ml/
│   ├── classifier.py          <- wrapper do modelo (carrega o teu .joblib)
│   └── train_model_example.py <- scaffold de treino (adapta ao teu modelo)
├── gns3/
│   └── topology_notes.md      <- como ligar o RYU aos switches no GNS3
└── requirements.txt
```

## Instalação

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> RYU é sensível a versões de `eventlet`/`dnspython` e funciona melhor com
> Python 3.8/3.9. Ver `gns3/topology_notes.md` para detalhes e workarounds
> se estiveres num Python mais recente.

## Correr o controlador

```bash
ryu-manager --ofp-tcp-listen-port 6653 controller/ai_ids_controller.py
```

Depois, na tua topologia GNS3, aponta cada switch Open vSwitch para o IP
da máquina onde isto está a correr, na porta 6653, em OpenFlow 1.3 — os
comandos exatos (`ovs-vsctl set-controller ...`) estão em
`gns3/topology_notes.md`.

## Ligar o teu modelo de IA

Disseste que já tens o código do modelo, só falta treinar. O fluxo pensado
para isso é:

1. **Não mexas no controlador para isto.** `ai_ids_controller.py` já sabe
   extrair features de cada fluxo e chamar `TrafficClassifier.predict(...)`
   — só precisas de garantir que existe um modelo treinado em
   `ml/model.joblib`.
2. Em `ml/classifier.py`, a lista `FEATURE_NAMES` define que features são
   extraídas das estatísticas OpenFlow (duração, nº de pacotes, bytes,
   taxa de pacotes/seg, protocolo IP, portas, etc.). Ajusta esta lista
   para bater certo com as features que o teu modelo espera.
3. Adapta `ml/train_model_example.py` (ou o teu próprio script) para
   treinar o teu modelo com essas mesmas features e guardar o resultado
   com `joblib.dump(modelo, "ml/model.joblib")`.
4. Se o teu modelo **não** for scikit-learn (ex: rede neuronal em
   PyTorch/TensorFlow), só precisas de substituir o método
   `_predict_with_model` em `ml/classifier.py` — o resto do controlador
   (extração de features, decisão de bloqueio, instalação da flow rule)
   não muda.

Enquanto não tiveres o modelo treinado, `classifier.py` usa automaticamente
um fallback baseado em regras simples (taxa de pacotes/bytes anormalmente
alta), só para conseguires testar já a parte de deteção → bloqueio
end-to-end na tua topologia GNS3 (por exemplo com um `ping -f` ou
`hping3 --flood` entre dois hosts).

## Ação ao detetar tráfego malicioso

Atualmente: **drop do fluxo específico** (regra OpenFlow com `actions=[]`,
prioridade 100, expira ao fim de 120s — ajustável em
`BLOCK_HARD_TIMEOUT`/`BLOCK_PRIORITY` no topo de `ai_ids_controller.py`).

Se mais tarde quiseres evoluir para bloquear o host de origem inteiro em
vez de só o fluxo, ou passar por uma fase de "só alerta, sem bloquear"
antes de confiares no modelo em produção, isso é uma mudança pequena em
`_handle_malicious_flow` — dá-me um toque quando chegares a essa fase que
ajusto o código.

## Próximos passos sugeridos

1. Validar a topologia GNS3 + ligação ao controlador (ver
   `gns3/topology_notes.md`).
2. Gerar tráfego normal e tráfego "malicioso" simulado na topologia para
   testar o classificador de fallback.
3. Treinar o modelo real e ligar via `ml/model.joblib`.
4. Ajustar o `threshold` do classificador e o `MIN_PACKETS_TO_CLASSIFY`
   para equilibrar falsos positivos vs. deteção (importante para a
   discussão de resultados na tese).
