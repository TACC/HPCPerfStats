# pull official base image
FROM python:3.12-trixie

# Setup Users and Directories
RUN useradd -u 901860  -ms /bin/bash hpcperfstats 
WORKDIR /home/hpcperfstats

# Setup working directories and get ssh-keys for rsync
RUN mkdir -p /hpcperfstats/
RUN mkdir -p /hpcperfstatslog/
RUN mkdir -p -m700 /home/hpcperfstats/.ssh/
RUN chown hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh/

# Upgrade the base OS and grab some important packages
RUN apt-get update -y && \
    apt-get upgrade -y && \
    apt-get install netcat-openbsd supervisor rsync syslog-ng  \
        vim net-tools lsof pigz nano npm nodejs -y

# Copy the package to the container
COPY --chown=hpcperfstats:hpcperfstats . .

# Keep the container updated everytime it is built, even when previous steps are cached
RUN apt-get update -y  && \
    apt-get upgrade -y

# install nodejs react dependencies and build the frontend
RUN cd hpcperfstats/site/frontend && npm install && \
    npm run build && cd .. && rm -rf hpcperfstats/site/frontend  && \
    apt-get remove -y npm nodejs && apt autoremove -y && apt-get clean

# Set python install variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PIP_ROOT_USER_ACTION ignore

# install version specific python dependencies and hpcperfstats package
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    pip cache purge
