FROM ubuntu:24.04

# Workaround for apt hash mismatches on arm64 mirrors
RUN echo 'Acquire::http::Pipeline-Depth "0";' > /etc/apt/apt.conf.d/99fixhash \
    && echo 'Acquire::http::No-Cache "True";' >> /etc/apt/apt.conf.d/99fixhash

# System packages
RUN apt-get update \
    && apt-get install -y git curl ca-certificates sudo vim python3-pip ntpdate \
    && rm -rf /var/lib/apt/lists/*

# Node.js (via NodeSource)
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# uv and agent user
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && useradd -m -s /bin/bash agent \
    && echo 'agent ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/agent \
    && chmod 440 /etc/sudoers.d/agent

# Claude Code + ACP wrappers
RUN npm install -g @anthropic-ai/claude-code @zed-industries/claude-code-acp @zed-industries/codex-acp

# Bridge venv (pre-cache dependencies so _deploy_bridge only writes bridge.py)
COPY bridge/pyproject.toml bridge/uv.lock /opt/druids/
RUN cd /opt/druids && uv sync --frozen -q --python-preference only-system

# Runner dependencies (execution_runner.py needs these at the system level)
RUN uv pip install --system --break-system-packages httpx uvicorn starlette

CMD ["tail", "-f", "/dev/null"]
