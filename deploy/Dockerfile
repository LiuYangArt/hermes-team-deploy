ARG HERMES_CORE_IMAGE
FROM ${HERMES_CORE_IMAGE}
USER root
COPY deploy/core-patches/exec-approval-request-id.patch /tmp/exec-approval-request-id.patch
RUN git -C /opt/hermes apply --check /tmp/exec-approval-request-id.patch && \
    git -C /opt/hermes apply /tmp/exec-approval-request-id.patch && \
    rm /tmp/exec-approval-request-id.patch
# Bake the platform extra into the image so startup never installs into shared state.
RUN rm -rf /opt/hermes/.venv && \
    python3 -m pm.build_env --source /opt/hermes --python /usr/local/bin/python3 \
    --out /opt/hermes/.venv --no-install-project --sealed \
    --extra all --extra messaging --extra otlp --extra anthropic --extra bedrock \
    --extra azure-identity --extra matrix --extra google-chat --extra feishu && \
    /opt/hermes/.venv/bin/python -m docker.build_agent && \
    /opt/hermes/.venv/bin/python -c 'import lark_oapi; from pm.extras import available; assert available("feishu")' && \
    chmod -R a+rX,go-w /opt/hermes/.venv && rm -f /opt/hermes/.venv/.lock
# JSON filtering and aggregation must not require executing generated Python.
RUN apt-get update && apt-get install -y --no-install-recommends jq && \
    rm -rf /var/lib/apt/lists/*
COPY deploy/official-tools.json deploy/install_official_tools.sh deploy/enable_team_plugins.py deploy/team-startup.sh /opt/hermes/
COPY extensions/lark-topics /opt/hermes/team-extensions/lark-topics
COPY extensions/lark-access /opt/hermes/team-extensions/lark-access
COPY extensions/personal-auth /opt/hermes/team-extensions/personal-auth
RUN sh /opt/hermes/install_official_tools.sh --image && \
    chmod 755 /opt/hermes/team-startup.sh && \
    cp /opt/hermes/team-startup.sh /etc/cont-init.d/02-hermes-team && \
    chmod -R a+rX /opt/hermes/official-skills /opt/hermes/team-extensions /usr/local/lib /usr/local/bin
