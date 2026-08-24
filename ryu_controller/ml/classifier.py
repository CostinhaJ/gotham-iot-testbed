"""
classifier.py
--------------
Wrapper em torno do modelo de IA/ML que classifica fluxos de rede como
"benigno" ou "malicioso" a partir de estatisticas OpenFlow.

Como usar com o TEU modelo:
1. Garante que o teu script de treino grava o modelo com joblib.dump(modelo, "model.joblib").
2. A ordem das features usada no treino TEM de ser exatamente a mesma
   definida em FEATURE_NAMES abaixo (ajusta a lista consoante o teu modelo).
3. Coloca o ficheiro treinado em ml/model.joblib (ou passa outro caminho
   ao instanciar TrafficClassifier).
4. Se ainda nao tiveres um modelo treinado, o classificador cai automaticamente
   num modo de fallback baseado em regras simples, para o controlador
   continuar a funcionar (e para poderes gerar trafego de teste) enquanto
   nao ligas o modelo real.

Se o teu modelo nao for scikit-learn (por exemplo, e uma rede neuronal em
PyTorch/TensorFlow), substitui apenas o metodo `_predict_with_model` por
baixo, mantendo a mesma assinatura de entrada/saida -- o resto do
controlador (extracao de features, instalacao de flow rules) nao precisa
de mudar.
"""

import logging
import os

import numpy as np

LOG = logging.getLogger("classifier")

# ---------------------------------------------------------------------------
# Ordem das features entregues ao modelo. Ajusta para bater certo com o
# dataset / script de treino que ja tens.
# ---------------------------------------------------------------------------
FEATURE_NAMES = [
    "duration_sec",       # duracao do fluxo em segundos
    "packet_count",       # numero de pacotes do fluxo
    "byte_count",         # numero de bytes do fluxo
    "avg_pkt_size",       # byte_count / packet_count
    "pkt_rate",           # packet_count / duration_sec  (pacotes/seg)
    "byte_rate",          # byte_count / duration_sec    (bytes/seg)
    "ip_proto",           # numero do protocolo IP (6=TCP, 17=UDP, 1=ICMP)
    "src_port",           # porta origem (0 se nao aplicavel)
    "dst_port",           # porta destino (0 se nao aplicavel)
]

DEFAULT_MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.joblib")

# Rotulos de saida esperados
LABEL_BENIGN = 0
LABEL_MALICIOUS = 1


class TrafficClassifier:
    """Carrega um modelo treinado (joblib/pickle) e classifica fluxos.

    Se nao existir modelo treinado no caminho indicado, usa um classificador
    de fallback baseado em limiares simples (pkt_rate/byte_rate elevados),
    apenas para nao bloquear o desenvolvimento do resto do pipeline.
    """

    def __init__(self, model_path=DEFAULT_MODEL_PATH, threshold=0.5):
        self.model_path = model_path
        self.threshold = threshold
        self.model = None
        self._load_model()

    def _load_model(self):
        if os.path.exists(self.model_path):
            try:
                import joblib

                self.model = joblib.load(self.model_path)
                LOG.info("Modelo carregado a partir de %s", self.model_path)
            except Exception as exc:  # noqa: BLE001
                LOG.error(
                    "Falha ao carregar modelo em %s (%s). "
                    "A usar classificador de fallback baseado em regras.",
                    self.model_path,
                    exc,
                )
                self.model = None
        else:
            LOG.warning(
                "Nenhum modelo treinado encontrado em %s. "
                "A usar classificador de fallback baseado em regras ate "
                "treinares e guardares o modelo real.",
                self.model_path,
            )

    def predict(self, features: dict):
        """Recebe um dicionario de features (ver FEATURE_NAMES) e devolve
        (label, score) em que label in {LABEL_BENIGN, LABEL_MALICIOUS} e
        score e a probabilidade/confianca de ser malicioso (0.0-1.0).
        """
        vector = np.array([[features.get(name, 0) for name in FEATURE_NAMES]])

        if self.model is not None:
            return self._predict_with_model(vector)
        return self._predict_fallback(features)

    def _predict_with_model(self, vector: np.ndarray):
        try:
            if hasattr(self.model, "predict_proba"):
                proba = self.model.predict_proba(vector)[0]
                score = float(proba[LABEL_MALICIOUS]) if len(proba) > 1 else float(proba[0])
            else:
                # Modelo sem predict_proba (ex: alguns classificadores) ->
                # usa a previsao direta como score binario.
                score = float(self.model.predict(vector)[0])
            label = LABEL_MALICIOUS if score >= self.threshold else LABEL_BENIGN
            return label, score
        except Exception as exc:  # noqa: BLE001
            LOG.error("Erro ao correr o modelo (%s). A usar fallback.", exc)
            return self._predict_fallback_from_vector(vector)

    @staticmethod
    def _predict_fallback(features: dict):
        """Heuristica simples so para testes sem modelo treinado:
        marca como suspeito trafego com taxa de pacotes ou bytes muito alta
        (possivel padrao de flood/DoS). Ajusta os limiares como quiseres.
        """
        pkt_rate = features.get("pkt_rate", 0)
        byte_rate = features.get("byte_rate", 0)

        if pkt_rate > 100 or byte_rate > 1_000_000:
            return LABEL_MALICIOUS, min(1.0, pkt_rate / 200)
        return LABEL_BENIGN, 0.0

    @classmethod
    def _predict_fallback_from_vector(cls, vector: np.ndarray):
        features = dict(zip(FEATURE_NAMES, vector[0].tolist()))
        return cls._predict_fallback(features)
