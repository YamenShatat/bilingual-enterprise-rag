# One image for the API and the UI (docker-compose.yml runs it twice with different commands):
# the UI does not need torch, but a second image would store the same layers again.
#
# Model weights are not in the image: docker-compose.yml mounts the host's Hugging Face cache
# read-only, and the LLM runs in Ollama on the host (D-027).

# Pinned by digest: the same tag can be republished with different contents.
FROM python:3.14.6-slim-trixie@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 app
WORKDIR /app

# PyTorch's CUDA 13.0 build first, in its own layer (about 3 GB of wheels): changing the
# application code does not download it again. The pip cache mount keeps wheels between builds.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cu130

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[embeddings,api,ui]" && rm -rf build

COPY scripts ./scripts
COPY data ./data
COPY .streamlit ./.streamlit

USER app

# Models come only from the mounted cache; nothing is downloaded at run time.
ENV HF_HOME=/models/huggingface \
    HF_HUB_OFFLINE=1

EXPOSE 8000 8501
CMD ["uvicorn", "bilingual_rag.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
