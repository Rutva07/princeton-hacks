FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
RUN pip install --no-cache-dir \
    numpy==2.2.6 pandas==2.2.3 scipy==1.15.3 scikit-learn==1.6.1 matplotlib==3.10.3
# CPU wheels avoid downloading CUDA libraries for network-disabled CPU jobs.
# https://pytorch.org/get-started/previous-versions/
RUN pip install --no-cache-dir torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu
RUN python -c "import numpy, pandas, scipy, sklearn, matplotlib, torch; assert torch.version.cuda is None; assert torch.ones(1).device.type == 'cpu'"
RUN useradd --uid 65532 --create-home --home-dir /nonexistent --shell /usr/sbin/nologin experiment
USER 65532:65532
WORKDIR /experiment

