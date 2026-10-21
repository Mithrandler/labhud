# Minimal image: Python with one dependency, plus a real `ping` and `openssl` (labhud makes
# its own HTTPS certificate with it at the first start, tlscert.py). The dependency is
# `cryptography`, for AES-256-GCM: the standard library has scrypt but no cipher, and an encrypted
# backup (api/backup.py) should use a vetted AEAD, not one made up here. Wheels for amd64 and arm64.
# busybox ping (the one in alpine) has no `-W`, so a dead host would block status collection.
FROM python:3.12-alpine
RUN apk add --no-cache iputils libcap openssl \
 # `ping` gets the capability on the file, so the process does not have to run as root.
 && setcap cap_net_raw+ep "$(command -v ping)" \
 && adduser -D -u 10001 labhud \
 # `labhud …` in the container (docker exec labhud labhud agent list): cli.py
 && printf '#!/bin/sh\nexec python3 /app/cli.py "$@"\n' > /usr/local/bin/labhud && chmod 755 /usr/local/bin/labhud
RUN pip install --no-cache-dir cryptography==50.0.2
WORKDIR /app
COPY admin.py agentproto.py alerting.py agents.py cardstate.py cardtype.py cli.py config.py devices.py iconcache.py editmode.py envfiles.py events.py init.py layout.py lock.py managed.py mqtt.py notify.py onboard.py qr.py routes.py scan.py screen.py server.py setupmode.py store.py thresholds.py tlscert.py tomlw.py updates.py util.py websock.py ./
COPY sources ./sources
COPY api ./api
COPY agents/labhud-agent.py agents/install.sh agents/install-agent.sh agents/agent-release.json ./agents/
COPY static ./static
COPY demo ./demo
# The app may also be mounted read-only over /app; without this Python complains it cannot write __pycache__.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Set by CI from the git tag (vX.Y.Z -> X.Y.Z); a local build is "dev". Last, so a new version
# does not invalidate the layers above.
ARG VERSION=dev
ENV LABHUD_VERSION=$VERSION
LABEL org.opencontainers.image.title="labhud" \
      org.opencontainers.image.description="A wall-mounted status display for your homelab" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/Mithrandler/labhud"
USER labhud
EXPOSE 8095
CMD ["python3", "/app/server.py"]
