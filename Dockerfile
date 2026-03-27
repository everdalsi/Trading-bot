# ================================================
# Dockerfile — Trading Bot v7.1 (version finale optimisée)
# ================================================

FROM python:3.11-slim

# Variables d'environnement pour un build propre et rapide
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GIT_PYTHON_REFRESH=quiet \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

# === 1. Installation des dépendances système (obligatoires pour matplotlib, crewai, gcc, git, etc.) ===
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    git \
    libpng-dev \
    libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/* \
    && git --version && echo "✅ git installé avec succès" \
    && echo "✅ Dépendances système prêtes pour matplotlib + agents"

# === 2. Copie des dépendances Python en premier (optimisation des layers Docker) ===
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && echo "✅ Toutes les dépendances Python installées (matplotlib, crewai, chromadb, flask, etc.)"

# === 3. Copie du code complet du bot ===
COPY . .

# === 4. Copie explicite du dossier templates (dashboard Claude Office) ===
COPY templates /workspace/templates

# === 5. Vérification finale du build (très utile pour Railway) ===
RUN python -c "import matplotlib; print('✅ matplotlib OK')" \
    && python -c "import crewai, langchain_groq, chromadb; print('✅ Agents IA OK')" \
    && echo "🎉 Build terminé avec succès — tout est chargé !"

EXPOSE 8000

# Démarrage du bot
CMD ["python", "bot.py"]
