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

# === 1. Installation des dépendances système ===
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

# === 2. Copie des dépendances Python + installation ultra-rapide ===
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --only-binary=:all: -r requirements.txt \
    && echo "✅ Toutes les dépendances Python installées (matplotlib, crewai, chromadb, flask, etc.)"

# === 3. Copie du code complet du bot ===
COPY . .

# === 4. Copie explicite du dossier templates (dashboard Claude Office) ===
COPY templates /workspace/templates

# === UPGRADE : Vérification du dossier knowledge (cours pro Wyckoff, VSA, CFA, Elder...) ===
RUN ls -la /workspace/knowledge/ 2>/dev/null || echo "⚠️ Dossier knowledge vide ou absent" \
    && echo "✅ Dossier knowledge vérifié (cours professionnels chargés)"

# === 5. Vérification finale du build ===
RUN python -c "import matplotlib; print('✅ matplotlib OK')" \
    && python -c "import crewai, langchain_groq, chromadb; print('✅ Agents IA OK')" \
    && python -c "
import os
if os.path.exists('/workspace/knowledge'):
    pdfs = [f for f in os.listdir('/workspace/knowledge') if f.lower().endswith('.pdf')]
    print(f'✅ {len(pdfs)} fichiers PDF pro trouvés dans knowledge/')
else:
    print('⚠️ Dossier knowledge non trouvé')
" \
    && echo "🎉 Build terminé avec succès — tout est chargé !"

EXPOSE 8000

# Démarrage du bot
CMD ["python", "bot.py"]
