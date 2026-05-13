FROM nikolaik/python-nodejs:python3.11-nodejs20

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV BROWSER_USE_CONFIG_DIR=/root/.browser-use
ENV BROWSER_USE_HEADLESS=true
ENV PROFILE_USE_HOME=/browser-profiles
ENV OBSIDIAN_VAULT=/vault/obsidian
ENV ANONYMIZED_TELEMETRY=false
ENV LUMA_BROWSER_USE_BIN=/usr/local/bin/browser-use
ENV SPECTER_BROWSER_USE_BIN=/usr/local/bin/browser-use
ENV PATH="/root/.browser-use/bin:${PATH}"

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    fonts-liberation \
    git \
    jq \
    tini \
    xauth \
    xdg-utils \
    xvfb \
  && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip \
  && python -m pip install --no-cache-dir browser-use playwright \
  && python -m pip check

RUN python -m playwright install --with-deps chromium

RUN browser-use install \
  && browser-use profile update \
  && ln -sf /root/.browser-use/bin/profile-use /usr/local/bin/profile-use

RUN mkdir -p /ms-playwright /root/.browser-use /root/.cache /browser-profiles /vault/obsidian \
  && chmod -R 0777 /browser-profiles /vault/obsidian /ms-playwright

WORKDIR /Users/pablote/Projects/growth_hacker_v7_2026

RUN browser-use doctor || true

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/bash"]
