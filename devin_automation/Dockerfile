#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
# Deliberately independent from the Superset image: this service only needs a
# handful of pure-Python packages and should start in seconds.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY devin_automation/requirements.txt /app/devin_automation/requirements.txt
RUN pip install --no-cache-dir -r /app/devin_automation/requirements.txt

COPY devin_automation /app/devin_automation

RUN useradd --create-home --uid 1000 automation && mkdir -p /data && \
    chown -R automation /data /app
USER automation

EXPOSE 8200

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; \
    urllib.request.urlopen('http://localhost:8200/health').read()"

CMD ["uvicorn", "devin_automation.main:app", "--host", "0.0.0.0", "--port", "8200"]
