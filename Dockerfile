# pull official base image
FROM python:3.6.15

RUN useradd -ms /bin/bash hpcperfstats
WORKDIR /home/hpcperfstats

# run as root
RUN apt-get update && apt-get upgrade -y
RUN apt-get install netcat supervisor rsync vim -y

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# install dependencies
RUN pip install --upgrade pip
COPY --chown=hpcperfstats:hpcperfstats ./requirements.txt .
RUN pip install -r requirements.txt

# Setup working directories and get ssh-keys for rsync
RUN mkdir -p /hpcperfstats/
RUN mkdir -p -m700 /home/hpcperfstats/.ssh/
RUN chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh/

# Setup supervisord for the pipeline
ADD services-conf/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Copy and install the hpcperfstats package
COPY --chown=hpcperfstats:hpcperfstats . .
RUN pip install .
