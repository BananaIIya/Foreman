FROM python:3.10
WORKDIR /app
# Snapshot of the stubbed base commit exported locally without .git (no reference history in the image).
COPY app/ /app/
ENV PYTHONPATH=/app
RUN pip install --no-cache-dir 'pytest>=7.2.0,<8.0.0' 'pytest-pycodestyle>=2.3.1,<3.0.0' 'pytest-cov>=4.0.0,<5.0.0' 'pycodestyle>=2.10.0,<3.0.0' 'sphinx>=7.0.0,<8.0.0' 'coveralls>=3.3.1,<4.0.0' 'pyyaml>=6.0,<7.0' 'types-PyYAML>=6.0.0,<7.0.0' 'typing-extensions>=4.4.0,<5.0.0' && pip install --no-cache-dir 'pytest<8.4' pytest-json-report
# No package index after the build: the original library cannot be pip-installed by the agent.
RUN printf '[global]\nno-index = true\n' > /etc/pip.conf
