# HPCPerfStats Design

HPCPerfStats is a package for viewing the performance of HPC jobs. 

##System Architecture

HPCPerfStats has two major components. The first is the monitor, which runs on each node in a cluster and collects node-level perfromance statistics. This is a standalone C program and an RPM spec file is included to help the installation.

###monitor
monitor facts


###web site
The persistent collection and display framework is designed to be deployed as a container orchestration using docker compose. The containers are:

####web
This container is generated from a Dockerfile in the code base. The container is a python 3.12 base and contains all packages and python libraries that are needed to run the persistent software for collecting and showing the performace data.

####pipeline
####proxy
####db
####rabbitmq


##Data Design/Flow

##Component Design and Software Configuration

##Assumptions and Dependencies

##Glossary



