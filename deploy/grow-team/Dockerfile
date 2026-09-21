FROM ghcr.io/zulip/zulip-server:12.2-0@sha256:765f0ab3caa49041989132ee1879d98dbab1df7695c27e713eac1f114d167755

ARG GROW_TEAM_REVISION
LABEL org.opencontainers.image.title="Grow Team" \
      org.opencontainers.image.source="https://github.com/Growth-Circle/grow-team" \
      org.opencontainers.image.revision="${GROW_TEAM_REVISION}"

WORKDIR /home/zulip/deployments/current
COPY --chown=zulip:zulip app/ ./
COPY assemble_image.py /opt/grow-team-build/assemble_image.py
RUN .venv/bin/python /opt/grow-team-build/assemble_image.py "${GROW_TEAM_REVISION}"

# Keep the upstream entrypoint and health check.
WORKDIR /root
