# The lobby server is pure Python stdlib -- no dependencies to install.
FROM python:3.12-slim
WORKDIR /app
COPY lobby_server.py .
EXPOSE 7788
CMD ["python", "lobby_server.py", "--host", "0.0.0.0", "--port", "7788"]
