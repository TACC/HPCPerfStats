# HPCPerfStats Design

HPCPerfStats is a package for viewing the performance of HPC jobs. 

## Component Inventory

HPCPerfStats has two major components. The first is the monitor, which runs on each node in a cluster and collects node-level perfromance statistics. This is a standalone C program and an RPM spec file is included to help the installation. The second is a docker-compose web site workflow, including multiple containers, that hosts all data proccessing and web related services. 

### monitor
The monitor is a C binary that collects node level data from many sources (MSRs, /proc, /sys, accelerator counters, network counters, etc) and collates that data into a singular message and sends that message to rabbitmq for processing. 

### web site
The persistent collection and display framework is designed to be deployed as a container orchestration using docker compose. The containers are:

#### web
This container is generated from a Dockerfile in the code base. The container is a python 3.12 base and contains all packages and python libraries that are needed to run the persistent software for collecting and showing the performace data. In this container instance gunicorn (python webserver) is run loading the DJango website.

#### pipeline
This container uses the same image as the web container. This container is responsible for running the "data pipeline" which injests raw data from rabbitmq and slurm, does some data clean up, inserts the data into the database and then creates secondary metrics from the data. Additionally, this container archives all the raw data and optionally sends it to an archive. All of the different scripts in the pipeline are coordinated by supervisord. 

#### proxy
This is a standard nginx container from dockerhub. It is configured to proxy SSL requests to the python webserver. It also redirects any non-SSL traffic to the proper SSL ports.

#### db
This is standard TimescaleDB/PostgresSQL container from dockerhub. This database is where all injested data ends up and where the web container reads all data for the website. 

#### rabbitmq
This is a standard RabbitMQ container from dockerhub. This rabbitmq instance is sent data from each monitor on each node. The data then waits for the pipeline to read the data off of the message queue.


## Data Design/Flow

### Terms
node-level data: This is raw performance data from the node and does not contain any job information. 

job-level data: This data defines what jobs have been run on the cluster, importantly, the time that the job starts and finishes as well as the nodes invovled in the job.

job-indexed data: This is the collated data where the node-level data is integrated with the job-level data to create a job performance data view, where the performance data is indexed and can be retrieved by job id. 


### Data flow in timeline order

On the login nodes, a script dumps the slurm job logs and this data is transfered to the pipeline container.

On each node in the cluster, the monitor collects performance data and sends it to RabbitMQ

RabbitMQ then holds the data until it is pulled off by "listen.py" script. This script takes each message and writes it out as a raw stats file to the archive directory with a specific directory structure: node_name/epoch_timestamp.

The "sync_timedb.py" script then reads this directory, takes the node-level data from raw messages and imports it to the database.

The "sync_acct.py" script reads the slurm job log file and imports the job-level data into the database. 

The "update_metrics.py" script then reads the database job-level and node-level data and creates job-indexed data. Additionally, it uses this job-index data to create a number of secondary metrics.

The website then loads the data from the DB, displays the data and plots data over time in a number of ways. 





