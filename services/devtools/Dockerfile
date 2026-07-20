# =============================================================================
# DevTools Runtime Container
# Git-focused development tools with Python support
# Platform-independent container for Git operations and development tools
# =============================================================================

FROM python:3.13-slim-trixie

# =============================================================================
# Custom Labels
# =============================================================================

# Metadata
LABEL vendor="BAUER GROUP"
LABEL maintainer="Karl Bauer <karl.bauer@bauer-group.com>"

# Opencontainers Metadata
LABEL org.opencontainers.image.title="DevTools"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.vendor="BAUER GROUP"
LABEL org.opencontainers.image.authors="Karl Bauer <karl.bauer@bauer-group.com>"
LABEL org.opencontainers.image.source="https://github.com/bauer-group/CS-DeveloperTools"
LABEL org.opencontainers.image.url="https://github.com/bauer-group/CS-DeveloperTools"
LABEL org.opencontainers.image.documentation="https://github.com/bauer-group/CS-DeveloperTools#readme"
LABEL org.opencontainers.image.description="Swiss Army Knife for Git-based Development"
LABEL org.opencontainers.image.version="0.0.0"

# =============================================================================
# System Dependencies
# =============================================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core utilities (bash already included in Debian)
    coreutils \
    grep \
    sed \
    findutils \
    # Git ecosystem
    git \
    git-lfs \
    openssh-client \
    gnupg \
    # Security & Crypto
    openssl \
    ca-certificates \
    # Build tools (for Python packages)
    gcc \
    libc6-dev \
    libffi-dev \
    # Network tools
    curl \
    wget \
    # JSON processing
    jq \
    # Docker CLI
    docker.io \
    docker-compose \
    # Editors
    nano \
    vim-tiny \
    # Extra utilities
    tree \
    less \
    ncurses-bin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install yq (not in Debian repos, install from GitHub)
RUN curl -sSL https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -o /usr/local/bin/yq \
    && chmod +x /usr/local/bin/yq

# Install GitHub CLI (gh)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# =============================================================================
# Python Dependencies
# =============================================================================
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# =============================================================================
# Git Configuration
# =============================================================================
# Safe directory für gemountete Volumes
RUN git config --system --add safe.directory '*'

# Hilfreiche Git-Aliase
RUN git config --system alias.st 'status -sb' && \
    git config --system alias.lg 'log --oneline --graph --decorate -20' && \
    git config --system alias.lga 'log --oneline --graph --decorate --all -30' && \
    git config --system alias.co 'checkout' && \
    git config --system alias.br 'branch -vv' && \
    git config --system alias.ci 'commit' && \
    git config --system alias.df 'diff' && \
    git config --system alias.dfs 'diff --staged' && \
    git config --system alias.last 'log -1 HEAD --stat' && \
    git config --system alias.unstage 'reset HEAD --' && \
    git config --system alias.amend 'commit --amend --no-edit' && \
    git config --system alias.undo 'reset --soft HEAD~1' && \
    git config --system alias.stash-all 'stash save --include-untracked' && \
    git config --system alias.branches 'branch -a --sort=-committerdate' && \
    git config --system alias.tags 'tag -l --sort=-version:refname' && \
    git config --system alias.contributors 'shortlog -sn --all' && \
    git config --system alias.file-history 'log --follow -p --'

# =============================================================================
# Shell Configuration
# =============================================================================
SHELL ["/bin/bash", "-c"]

# Custom bash prompt with git branch
RUN echo 'parse_git_branch() { git branch 2>/dev/null | grep "^*" | sed "s/* //"; }' >> /etc/profile.d/devtools.sh && \
    echo 'export PS1="\[\033[1;34m\]devtools\[\033[0m\]:\[\033[1;32m\]\w\[\033[0m\] \[\033[1;33m\]\$(parse_git_branch)\[\033[0m\]\$ "' >> /etc/profile.d/devtools.sh && \
    echo 'export TERM=xterm-256color' >> /etc/profile.d/devtools.sh && \
    echo 'export PATH="/opt/devtools/bin:/opt/devtools/scripts:$PATH"' >> /etc/profile.d/devtools.sh && \
    echo 'export GH_CONFIG_DIR="/data/gh"' >> /etc/profile.d/devtools.sh && \
    echo 'alias ll="ls -la --color=auto"' >> /etc/profile.d/devtools.sh && \
    echo 'alias la="ls -A --color=auto"' >> /etc/profile.d/devtools.sh && \
    echo 'alias l="ls -CF --color=auto"' >> /etc/profile.d/devtools.sh

# Data directory for persistent config (GitHub auth, etc.)
RUN mkdir -p /data/gh
ENV GH_CONFIG_DIR="/data/gh"

# =============================================================================
# Scripts Directory
# =============================================================================
COPY scripts/ /opt/devtools/scripts/

# Strip CR from the build context. .gitattributes pins *.sh and *.py to LF, but
# clones created before that rule still carry CRLF in the working tree, and a
# CRLF shebang makes the kernel look for an interpreter named "python3\r".
# Normalising here keeps the image correct regardless of host checkout state.
RUN find /opt/devtools/scripts -type f \( -name '*.sh' -o -name '*.py' \) \
        -exec sed -i 's/\r$//' {} +

RUN chmod +x /opt/devtools/scripts/*.sh /opt/devtools/scripts/*.py 2>/dev/null || true

# Create symlinks without extensions in separate bin directory
# e.g., /opt/devtools/bin/git-stats -> ../scripts/git-stats.sh
RUN mkdir -p /opt/devtools/bin && \
    for script in /opt/devtools/scripts/*.sh /opt/devtools/scripts/*.py; do \
        [ -f "$script" ] || continue; \
        base=$(basename "$script"); \
        name="${base%.*}"; \
        ln -sf "../scripts/$base" "/opt/devtools/bin/$name"; \
    done

ENV PATH="/opt/devtools/bin:/opt/devtools/scripts:${PATH}"

# =============================================================================
# Working Directory
# =============================================================================
WORKDIR /workspace

# Entrypoint für Initialisierung
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/bin/bash", "-l"]
