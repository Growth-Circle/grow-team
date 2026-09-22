FROM node:24.18.0-trixie-slim@sha256:ae91dcc111a68c9d2d81ff2a17bda61be126426176fde6fe7d08ab13b7f50573
COPY node_modules /opt/grow/node_modules
COPY dist /opt/grow/dist
COPY protocol /opt/grow/protocol
COPY native /opt/grow/native
COPY notices /opt/grow/notices
USER 65532:0
WORKDIR /workspace
ENV HOME=/tmp/home LANG=C.UTF-8
