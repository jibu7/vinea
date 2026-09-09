FROM python:3.12-slim
WORKDIR /app
# WeasyPrint renders the AR/AP statement PDFs and needs pango/cairo at runtime.
#
# `libglib2.0-0` and `git` are here so the dev container fails the same way CI does. Without
# them three tests failed locally and passed in CI — WeasyPrint's `find_library('gobject-2.0-0')`
# had nothing to resolve, and `test_postgres_data_directories_are_ignored` shells out to git.
# A local suite that is green for reasons CI is not tells you nothing, which is worse than red.
#
# `ldconfig` runs explicitly: on a slim image `ctypes.util.find_library` reads the ldconfig
# cache, and an unbuilt cache makes a present .so invisible.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi8 \
      libglib2.0-0 fonts-dejavu-core git \
 && ldconfig \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev || uv sync
COPY . .
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
