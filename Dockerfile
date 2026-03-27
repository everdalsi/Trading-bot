FROM python:3.11-slim

# 1. Installation des outils de compilation (obligatoire pour sgmllib3k et autres)
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

# 3. Installation des dépendances (SANS le flag --only-binary)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copie du reste du code
COPY . .

# 5. Vérification
RUN python -c "import feedparser; print('✅ Feedparser OK')" \
    && python -c "import crewai; print('✅ CrewAI OK')"

EXPOSE 8000

CMD ["python", "bot.py"]
