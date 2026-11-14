FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 10001 docs && install -d -o docs -g docs -m 0700 /srv/docs
COPY internal_documents ./internal_documents
ENV DOCS_DATA=/srv/docs
USER docs
EXPOSE 8000
CMD ["uvicorn","internal_documents.app:create","--factory","--host","0.0.0.0","--port","8000","--no-access-log"]
