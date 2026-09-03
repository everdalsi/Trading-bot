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

# 3. Installation des dépendances
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copie du reste du code
COPY . .

# 5. Vérification post-install (robuste – pyfolio-reloaded est optionnel pour éviter les plantages)
RUN python -c "import feedparser; print('✅ Feedparser OK')" \
    && python -c "import crewai; print('✅ CrewAI OK')" \
    && python -c "import vectorbt as vbt; print('✅ VectorBT OK')" \
    && (python -c "import quantstats as qs; print('✅ QuantStats OK')" || echo "⚠️  QuantStats non importable (optionnel, casse sur des incompatibilites plotly/scattermapbox transitoires -- pas utilise par bot.py, voir agents/code_fixer_agent.py seulement)") \
    && python -c "import redis; print('✅ Redis OK')" \
    && (python -c "import pyfolio as pf; print('✅ Pyfolio-reloaded OK')" || echo "⚠️  Pyfolio-reloaded non importable (optionnel)") \
    && echo "✅ Toutes les dépendances critiques Phase 1 sont installées avec succès !"

EXPOSE 8000

# Commande de démarrage
CMD ["python", "bot.py"]
