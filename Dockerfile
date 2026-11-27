# MCP server image (used by the Glama registry to inspect the tools).
# Serves the bundled example; mount your own file and pass its path to use another:
#   docker run -i --rm -v "$PWD:/data" plainontology /data/my.onto
FROM python:3.13-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[mcp]"
ENTRYPOINT ["plainonto", "mcp"]
CMD ["examples/library.onto"]
