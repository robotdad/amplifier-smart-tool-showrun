FROM ubuntu:24.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-gi python3-pyatspi gir1.2-gtk-3.0 xvfb xauth dbus-x11 \
    && rm -rf /var/lib/apt/lists/*
ENV NO_AT_BRIDGE=0
WORKDIR /fixture
# Mount this fixture directory read-only at /fixture and writable trial output at /output.
CMD ["dbus-run-session", "--", "xvfb-run", "-a", "python3", "linux/atspi_trial.py", "--output", "/output"]
