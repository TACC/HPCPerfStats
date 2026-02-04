# HPCPerfStats Design

HPCPerfStats is a package for viewing the performance of HPC jobs. 

## System Architecture

HPCPerfStats has two major components. The first is the monitor, which runs on each node in a cluster and collects node-level perfromance statistics. This is a standalone C program and an RPM spec file is included to help the installation.

### monitor
The monitor is a C binary that collects node level data from many sources (MSRs, /proc, /sys, accelerator counters, network counters, etc) and collates that data into a singular message and sends that message to rabbitmq for processing. 

### web site
The persistent collection and display framework is designed to be deployed as a container orchestration using docker compose. The containers are:

#### web
This container is generated from a Dockerfile in the code base. The container is a python 3.12 base and contains all packages and python libraries that are needed to run the persistent software for collecting and showing the performace data. In this container instance gunicorn (python webserver) is run loading the DJango website.

#### pipeline
This container uses the same image as the web container. This container is responsible for running the "data pipeline" which injests raw data from rabbitmq and slurm, does some data clean up, inserts the data into the database and then creates secondary metrics from the data. Additionally, this container archives all the raw data and optionally sends it to an archive. 

#### proxy
This is a standard nginx container from dockerhub. It is configured to proxy SSL requests to the python webserver. It also redirects any non-SSL traffic to the proper SSL ports.

#### db
This is standard TimescaleDB/PostgresSQL container from dockerhub. This database is where all injested data ends up and where the web container reads all data for the website. 

#### rabbitmq
This is a standard RabbitMQ container from dockerhub. This rabbitmq instance is sent data from each monitor on each node. The data then waits for the pipeline to read the data off of the message queue.


## Data Design/Flow



## Component Design and Software Configuration

## Assumptions and Dependencies

## Glossary



