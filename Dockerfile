FROM python:3.11-slim

# Évite les questions interactives pendant l'install
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace

# ── Installe les dépendances système une seule fois ──────────
# Cette couche est mise en cache tant que rien ne change ici
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

# ── Copie UNIQUEMENT requirements.txt d'abord ────────────────
# Docker ne réinstalle les packages QUE si requirements.txt change
# Si seulement bot.py change → cette couche reste en cache → build <30s
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Copie le reste du code ────────────────────────────────────
# Seule cette étape se re-exécute quand bot.py change
COPY . .

EXPOSE 8000

CMD ["python", "bot.py"]
