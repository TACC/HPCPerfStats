# pull official base image
FROM python:3.6.15

RUN useradd -ms /bin/bash hpcstats
WORKDIR /home/hpcstats

# run as root
RUN apt-get update && apt-get upgrade -y
RUN apt-get install netcat supervisor -y


USER hpcstats
# run as user
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PATH $PATH:/home/hpcstats/.local/bin
# install dependencies
RUN pip install --upgrade pip
COPY --chown=hpcstats:hpcstats ./requirements.txt .
RUN pip install -r requirements.txt

# copy project
COPY --chown=hpcstats:hpcstats . .
RUN pip install .

COPY --chown=hpcstats:hpcstats ./tacc_stats.ini .

ADD supervisord.conf /etc/supervisor/conf.d/supervisord.conf

