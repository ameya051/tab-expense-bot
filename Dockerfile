FROM python:3.12-slim

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Cleanup must happen in the same layer as the install to actually shrink the image.
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && find /usr/local/lib/python3.12/site-packages \
      \( -type d -name tests -o -type d -name test \) -prune -exec rm -rf {} + \
 && rm -rf /usr/local/lib/python3.12/site-packages/matplotlib/mpl-data/sample_data

COPY . .

RUN useradd --create-home --uid 1000 app
USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
