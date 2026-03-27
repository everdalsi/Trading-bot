FROM python:3.11-slim

# Installation des dépendances système nécessaires à la compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    python3-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Mise à jour de pip
RUN pip install --no-cache-dir --upgrade pip

# Installation des dépendances (sans la restriction binaire)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code
COPY . .

# Vérifications finales
RUN python -c "import crewai; print('✅ CrewAI OK')" \
    && python -c "import chromadb; print('✅ ChromaDB OK')"

EXPOSE 8000

CMD ["python", "bot.py"]
