# pull official base image
FROM python:3.12

RUN useradd -u 901860  -ms /bin/bash hpcperfstats 
WORKDIR /home/hpcperfstats

# Setup working directories and get ssh-keys for rsync
RUN mkdir -p /hpcperfstats/
RUN mkdir -p /hpcperfstatslog/
RUN mkdir -p -m700 /home/hpcperfstats/.ssh/
RUN chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh/

# Upgrade the base OS and grab some important packages
RUN apt-get update -y 
RUN apt-get upgrade -y
RUN apt-get install netcat-openbsd supervisor rsync syslog-ng vim net-tools lsof pigz -y

# install dependencies
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PIP_ROOT_USER_ACTION ignore

RUN pip install --upgrade pip

COPY --chown=hpcperfstats:hpcperfstats ./requirements.txt .
RUN pip install -r requirements.txt

# Copy and install the hpcperfstats package
COPY --chown=hpcperfstats:hpcperfstats . .
RUN pip install .

# Keep the container updated everytime it is built, even when previous steps are cached
RUN apt-get update -y 
RUN apt-get upgrade -y
