# Minimal image: Python with no extra dependency, plus a real `ping`.
# busybox ping (the one in alpine) has no `-W`, so a dead host would block status collection.
FROM python:3.12-alpine
RUN apk add --no-cache iputils libcap \
 # `ping` gets the capability on the file, so the process does not have to run as root.
 && setcap cap_net_raw+ep "$(command -v ping)" \
 && adduser -D -u 10001 labhud
WORKDIR /app
COPY config.py envfiles.py events.py init.py notify.py server.py store.py ./
COPY sources ./sources
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
