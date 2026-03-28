# Base image mise à jour en Python 3.12-slim pour meilleure compatibilité avec les upgrades Phase 1
# (pandas-ta-remake, vectorbt, crewai récentes fonctionnent mieux)
FROM python:3.12-slim

# 1. Installation des outils de compilation (obligatoire pour chromadb, vectorbt, crewai, sgmllib3k, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    libffi-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 2. Mise à jour de PIP (plus rapide et stable)
RUN pip install --no-cache-dir --upgrade pip

# 3. Installation des dépendances (requirements corrigé)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copie du reste du code source
COPY . .

# 5. Vérification post-install (pour debug rapide dans les logs Docker)
RUN python -c "import feedparser; print('✅ Feedparser OK')" \
    && python -c "import crewai; print('✅ CrewAI OK')" \
    && python -c "import vectorbt as vbt; print('✅ VectorBT OK')" \
    && python -c "import pandas_ta_remake as ta; print('✅ pandas-ta-remake OK')" \
    && echo "✅ Toutes les dépendances critiques sont installées avec succès"

EXPOSE 8000

# Commande de démarrage (adapte si tu as un entrypoint différent)
CMD ["python", "bot.py"]
