FROM python:3.12-slim
RUN pip install --no-cache-dir "arq==0.28.0" "procrastinate==3.10.0" "psycopg[binary,pool]==3.2.12" "psutil==7.1.3"
WORKDIR /spike
COPY *.py ./
