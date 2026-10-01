ARG HERMES_CORE_IMAGE
FROM ${HERMES_CORE_IMAGE}
USER root
# Bake the platform extra into the image so startup never installs into shared state.
RUN rm -rf /opt/hermes/.venv && \
    python3 -m pm.build_env --source /opt/hermes --python /usr/local/bin/python3 \
    --out /opt/hermes/.venv --no-install-project --sealed \
    --extra all --extra messaging --extra otlp --extra anthropic --extra bedrock \
    --extra azure-identity --extra matrix --extra google-chat --extra feishu && \
    /opt/hermes/.venv/bin/python -m docker.build_agent && \
    /opt/hermes/.venv/bin/python -c 'import lark_oapi; from pm.extras import available; assert available("feishu")' && \
    chmod -R a+rX,go-w /opt/hermes/.venv && rm -f /opt/hermes/.venv/.lock
COPY official-tools.json install_official_tools.sh /opt/hermes/
RUN sh /opt/hermes/install_official_tools.sh --image && \
    chmod -R a+rX /opt/hermes/official-skills /usr/local/lib /usr/local/bin
