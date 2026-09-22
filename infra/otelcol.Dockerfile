FROM ros@sha256:75dd3aba34a3838dadbb31a9f7bef769bdfa8713e6cec686fc868db2981b0987
ADD https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/v0.136.0/otelcol-contrib_0.136.0_linux_arm64.tar.gz /tmp/otelcol.tar.gz
RUN mkdir -p /otel && tar -xzf /tmp/otelcol.tar.gz -C /otel && rm /tmp/otelcol.tar.gz
ENTRYPOINT ["/otel/otelcol-contrib"]
