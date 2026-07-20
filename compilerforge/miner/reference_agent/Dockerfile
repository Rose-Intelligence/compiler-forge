# The reference optimization agent, as an artifact the validator can run.
#
# Build from an allowlisted base image and pin the lockfile. The
# resulting image is referenced by sha256 digest in the on-chain commitment;
# nothing is ever pulled by tag.
#
#   docker build -t ghcr.io/you/cf-reference-agent .
#   docker push    ghcr.io/you/cf-reference-agent
#   cf-miner submit --image ghcr.io/you/cf-reference-agent --netuid <netuid>
#
# The image carries no model weights: the validator mounts the pinned model and
# the repository at runtime, and inference goes through the metered proxy.

FROM docker.io/library/debian:bookworm-slim

# Everything the agent needs to reproduce the baseline and measure it the same way
# the validator's Tier A does. A harness that optimizes against wall-clock on a
# shared machine is optimizing against noise.
RUN apt-get update && apt-get install -y --no-install-recommends \
        clang \
        cmake \
        make \
        valgrind \
        python3 \
        python3-pip \
        diffutils \
        patch \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages --no-cache-dir httpx==0.27.2

WORKDIR /agent
COPY agent.py inference.py /agent/
COPY __init__.py /agent/compilerforge/miner/reference_agent/__init__.py

# The container runs non-root with a read-only root filesystem, all capabilities
# dropped and no network. /output is the only writable mount it is given.
RUN useradd --uid 65534 --no-create-home --shell /usr/sbin/nologin cfagent || true
USER 65534:65534

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CF_AGENT_WORKDIR=/tmp/cf-agent

# One command, reading /task/task.json and writing /output/{patch.diff,report.json}.
ENTRYPOINT ["python3", "/agent/agent.py"]
