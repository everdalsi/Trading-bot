# Base image Python 3.12-slim optimisée pour les upgrades Phase 1
FROM python:3.12-slim

# 1. Installation des outils de compilation (obligatoire pour chromadb, vectorbt, crewai, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    libffi-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 2. Mise à jour de PIP
RUN pip install --no-cache-dir --upgrade pip

# 2b. torch CPU only for the Kronos agent (06/11): the default PyPI wheel pulls CUDA (several GB); this one is ~200 MB
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir einops==0.8.1 huggingface_hub safetensors tqdm

# 3. Installation des dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copie du reste du code
COPY . .

# 5. Vérification post-install (robuste – pyfolio-reloaded est optionnel pour éviter les plantages)
RUN python -c "import feedparser; print('✅ Feedparser OK')" \
    && python -c "import crewai; print('✅ CrewAI OK')" \
    && (python -c "import vectorbt as vbt; print('✅ VectorBT OK')" || echo "⚠️  VectorBT non importable (optionnel, meme incompatibilite plotly/scattermapbox que QuantStats -- pas utilise par bot.py, voir agents/code_fixer_agent.py seulement)") \
    && (python -c "import quantstats as qs; print('✅ QuantStats OK')" || echo "⚠️  QuantStats non importable (optionnel, casse sur des incompatibilites plotly/scattermapbox transitoires -- pas utilise par bot.py, voir agents/code_fixer_agent.py seulement)") \
    && python -c "import redis; print('✅ Redis OK')" \
    && (python -c "import pyfolio as pf; print('✅ Pyfolio-reloaded OK')" || echo "⚠️  Pyfolio-reloaded non importable (optionnel)") \
    && echo "✅ Toutes les dépendances critiques Phase 1 sont installées avec succès !"

EXPOSE 8000

# audit 06/11: a frozen bot used to stay "Up". Healthy = the decision journal moved in the last 10 min
# (every live agent writes agent_events.jsonl; the Coordinator pulses each minute).
HEALTHCHECK --interval=2m --timeout=10s --start-period=10m --retries=3   CMD python -c "import os,sys,time; p=os.path.join(os.environ.get('DATA_DIR','/app/data'),'agent_events.jsonl'); sys.exit(0 if time.time()-os.path.getmtime(p) < 600 else 1)"

# Commande de démarrage
CMD ["python", "bot.py"]
