FROM ubuntu:24.04

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    PATH=/venv/bin:$PATH \
    DISPLAY=:99 \
    BROWSER_WINDOW=480,1000 \
    DATA_DIR=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
      xvfb x11vnc novnc fonts-dejavu-core fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python3 -m venv /venv \
 && pip install --no-cache-dir -r /app/requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY app/ /app/
COPY run.sh /run.sh
RUN chmod +x /run.sh

WORKDIR /app
CMD ["/run.sh"]
