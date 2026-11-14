FROM hashicorp/terraform:1.9 AS terraform

FROM python:3.12-slim
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY terraform /srv/terraform
ENV DATA_DIR=/srv/data TERRAFORM_DIR=/srv/terraform
RUN useradd --create-home portal && mkdir -p /srv/data && chown portal /srv/data
USER portal
EXPOSE 8000
# One worker on purpose: jobs and log streams live in this process.
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]
