FROM python:3.10
WORKDIR /app
# Snapshot of the stubbed base commit exported locally without .git (no reference history in the image).
COPY app/ /app/
ENV PYTHONPATH=/app/src
RUN pip install --no-cache-dir -r requirements-dev.txt && pip install --no-cache-dir 'py' && pip install --no-cache-dir 'pytest<8.4' pytest-json-report
# No package index after the build: the original library cannot be pip-installed by the agent.
RUN printf '[global]\nno-index = true\n' > /etc/pip.conf
