FROM python:3.11-slim

# Evita que Python genere archivos .pyc y asegura que la salida por consola no se bloquee
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# psycopg2-binary trae sus propias librerías: no hace falta compilar nada
# Se instalan también las dependencias de prueba para poder ejecutar pytest dentro del contenedor
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# Copiar el resto del código del worker/api
COPY . .

# El worker reutiliza esta imagen: docker-compose.yml sobrescribe el comando con
# ["python", "-m", "worker.sync"]. Por defecto arranca la API.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
