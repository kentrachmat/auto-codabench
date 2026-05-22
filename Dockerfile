# Dockerfile for the AutoCodabench Chainlit app — used by Hugging Face
# Spaces (Docker SDK). HF auto-detects this at the repo root.
#
# Local test (optional):
#   docker build -t autocodabench-web .
#   docker run -p 7860:7860 \
#     -e ANTHROPIC_API_KEY=sk-... -e SHARED_PASSWORD=... \
#     -e OPENALEX_MAILTO=... -e CODABENCH_USERNAME=... \
#     -e CODABENCH_PASSWORD=... -e CHAINLIT_AUTH_SECRET=... \
#     autocodabench-web

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git is needed by claude-agent-sdk for git-aware behavior;
# build-essential helps if any wheel needs to compile from sdist.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential && \
    rm -rf /var/lib/apt/lists/*

# HF Spaces runs as a non-root user (uid 1000). Create a matching one so
# pip + writing to /app/auto_codabench/runs/ works without permission errors.
RUN useradd -m -u 1000 user
WORKDIR /app

# Copy everything (the build context is the repo root). Adjust .dockerignore
# to keep the image small.
COPY --chown=user:user . /app

# Install the two MCP packages so that
# `python -m alex_mcp.server` and `python -m auto_codabench.mcp_server.server`
# work as the agent SDK spawns them.
#   - alex-mcp: pinned upstream tag (the previously-vendored alex-mcp/ tree
#     got corrupted; we install directly from GitHub now — it's not on PyPI).
#   - auto_codabench: editable from the repo (our own code).
RUN pip install --upgrade pip && \
    pip install 'git+https://github.com/drAbreu/alex-mcp.git@v4.8.2' && \
    pip install -e . && \
    pip install -r web/requirements.txt && \
    chown -R user:user /app

# Belt-and-suspenders: force the exact fastmcp version that imports cleanly,
# *after* every other install that might have pulled a transitive copy. The
# pin in pyproject.toml + requirements.txt should already do this, but HF
# layer-caching has bitten us before — and a stale subpackage layout in
# `fastmcp.server.auth.oauth_proxy` makes every MCP server fail to start.
# Echo the resolved version so it lands in the build log.
RUN pip install --no-cache-dir --force-reinstall --no-deps fastmcp==2.14.7 && \
    python -c "import fastmcp, pathlib; \
print('fastmcp at build time:', fastmcp.__version__, '@', fastmcp.__file__); \
print('oauth_proxy is:', pathlib.Path(fastmcp.__file__).parent / 'server/auth/oauth_proxy.py'); \
print('  exists as file?', (pathlib.Path(fastmcp.__file__).parent / 'server/auth/oauth_proxy.py').is_file())"

USER user
ENV HOME=/home/user

# HF Spaces injects $PORT; default to 7860 for local runs.
EXPOSE 7860

# Chainlit needs to be run from web/ so it picks up .chainlit/config.toml
# and chainlit.md. The app itself bootstraps PYTHONPATH for the rest of
# the repo (see web/app.py top).
WORKDIR /app/web
CMD ["sh", "-c", "chainlit run app.py --host 0.0.0.0 --port ${PORT:-7860}"]
