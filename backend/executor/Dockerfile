FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
RUN pip install --no-cache-dir \
    numpy==2.2.6 pandas==2.2.3 scipy==1.15.3 scikit-learn==1.6.1 matplotlib==3.10.3
RUN useradd --uid 65532 --create-home --home-dir /nonexistent --shell /usr/sbin/nologin experiment
USER 65532:65532
WORKDIR /experiment

