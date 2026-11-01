FROM python:3.12-slim
RUN pip install --no-cache-dir claude-agent-sdk==0.2.176
COPY agent.py /app/agent.py
COPY fixture.txt /workspace/fixture.txt
ENTRYPOINT ["python", "/app/agent.py"]
