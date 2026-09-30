FROM python:3.11-slim

# Empêcher Python de générer des fichiers .pyc
ENV PYTHONDONTWRITEBYTECODE=1
# Désactiver la mise en buffer de la sortie standard et d'erreur
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Installation des dépendances système nécessaires pour psycopg2 et pandas (build)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copier et installer les dépendances
COPY requirements.txt /app/
RUN pip install --no-cache-dir -U pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copier le code source de l'application
COPY . /app/

# Port pour l'API Flask/Gunicorn
EXPOSE 5000

# Commande par défaut (peut être surchargée par docker-compose)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", "--timeout", "120", "app:app"]
