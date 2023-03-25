# build stage
FROM python:3.10-bullseye as build

RUN set -x \
    && apt-get update \
    && apt-get install --no-install-recommends -y build-essential git libpq-dev libssl-dev

RUN set -x \
    && python3 -m venv /opt/red-githubbot

ENV PATH="/opt/red-githubbot/bin:${PATH}"

RUN pip --no-cache-dir --disable-pip-version-check install --upgrade pip setuptools wheel

COPY requirements.txt /tmp/requirements.txt

RUN set -x \
    && pip --no-cache-dir --disable-pip-version-check install --no-deps -r /tmp/requirements.txt \
    && pip check \
    && find /opt/red-githubbot -name '*.pyc' -delete


# actual application image
FROM python:3.10-bullseye

ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH /opt/red-githubbot/
ENV PATH="/opt/red-githubbot/bin:${PATH}"

WORKDIR /opt/red-githubbot/src/

RUN set -x \
    && apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY --from=build /opt/red-githubbot/ /opt/red-githubbot/
COPY . /opt/red-githubbot/src/

ENTRYPOINT python3 -Om red_githubbot
