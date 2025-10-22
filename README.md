HPCPerfStats
============
(The package formerly known as TACC Stats)

Description
============
The hpcperfstats package provides the tools to monitor resource usage of HPC systems at multiple levels of resolution.

[Collected Data Definitions](docs/attributes-definition.md)

The package is split into an `autotools`-based `monitor` subpackage and a Python `setuptools`-based `hpcperfstats` subpackage. The `monitor` performs the online data collection and transmisson in a production environment while `hpcperfstats` performs the data curation and analysis apart from the HPC cluster.

Building and installing the `hpcperfstats-2.4-1.el9.x86_64.rpm` package with the `hpcperfstats.spec` file will build and install a systemd service `hpcperfstats`.  This service launches a daemon with an overhead of 3% on a single core when configured to sample at a frequency of 1Hz.  It is typically configured to sample at 5 minute intervals, with samples taken at the start and end of every job as well. The HPCPerfStats daemon, `hpcperfstatsd`, is controlled by the `hpcperfstats` service and sends the data directly to a RabbitMQ server over the administrative ethernet network.  RabbitMQ must be installed and running on the server in order for the data to be received.

Installing the `hpcperfstats` container orchestration will setup a Django/PostgresGRE data ingest and archival tools, and a rabbitmq server to recieve data from the `monitor` on the nodes.

Installation
======
## `monitor` subpackage

First ensure the RabbitMQ library and header file are installed on the build and compute nodes

`sudo dnf install librabbitmq-devel`

`./configure; make; make install` will then successfully build the `hpcperfstatsd` executable for many systems.  If Xeon Phi coprocessors are present on your system they can be monitored with the `--enable-mic` flag.  Additionally the configuration options, `--disable-infiniband`, `--disable-lustre`, `--disable-hardware` will disable infiniband, Lustre Filesystem, and Hardware Counter monitoring which are all enabled by default. Disabling RabbitMQ will result in a legacy build of `hpcperfstatsd` that relies on the shared filesystem to transmit data.  This mode is not recommended and currently used for testing purposes only.  If libraries or header files are not found than add their paths to the include and library paths with the `CPPFLAGS` and/or `LDFLAGS` vars as is standard in autoconf based installations.  

There will be a configuration file, `/etc/hpcperfstats/hpcperfstats.conf`, after installation.  This file contains the fields

`server localhost`

`queue default`

`port 5672`

`freq 600`


`server` should be set to the hostname or IP hosting the RabbitMQ server, `queue` to the system/cluster name that is being monitored, `port` to the RabbitMQ port (5672 is default), and `freq` to the desired sampling frequency in seconds. The file and settings can be reloaded into a running `hpcperfstatsd` daemon with a SIGHUP signal.

An RPM can be built for deployment using  the `hpcperfstats.spec` file.  The most straightforward approach to build this is to setup your rpmbuild directory then run

`rpmbuild -bb hpcperfstats.spec`

The `hpcperfstats.spec` file `sed`s the `hpcperfstats.conf` file to the correct server and queue. These can be changed by modifying these two lines 

`sed -i 's/localhost/stats.frontera.tacc.utexas.edu/' src/hpcperfstats.conf`

`sed -i 's/default/frontera/' src/hpcperfstats.conf`

`hpcperfstatsd` can be started, stopped, and restarted using `systemctl start hpcperfstats`, `systemctl stop hpcperfstats`, and `systemctl restart hpcperfstats`.

## Job Scheduler Configuration
In order to notify `hpcperfstats` of a job beginning, echo the job id into `/var/run/stats_jobid` on each node where the job is running.  It order to notify
it of a job ending echo `-` into `/var/run/stats_jobid` on each node where the job is running.  This can be accomplished in the job scheduler prolog and
epilog for example.

Additionally, in order to contextualize node-level data from the monitor package it is necessary to generate a daily accounting file that contains the following information about all jobs from that day in the following format:\

`JobID|User|Account|Start|End|Submit|Partition|Timelimit|JobName|State|NNodes|ReqCPUS|NodeList`
`1837137|sharrell|project140208|2018-08-01T18:18:51|2018-08-02T11:44:51|2018-07-29T08:05:43|normal|1-00:00:00|jobname|COMPLETED|8|104|c420-[024,073],c421-[051-052,063-064,092-093]`

If you are using SLURM the `sacct_gen.py` script that will be installed with the `hpcperfstats` subpackage may be used. 
This script generates a file for each date with the name format `year-month-day.txt`, e.g. `2018-11-01.txt`.

If you need to transfer this file accounting from another machine, please see the steps below for rsyncing data into the container data pipeline.

## `hpcperfstats` subpackage
The `hpcperfstats` subpackage is a container orchestration that includes a Django/PostgresSQL website, data ingest and archival tools, and a rabbitmq server to recieve data from the `monitor` on the nodes. The install instructions assume you are starting with a Rocky Linux host.

Install basic docker/podman components\
`sudo dnf install docker git podman-compose`

Setup podman to restart any running containers after reboot\
`sudo systemctl enable podman-restart.service`
`sudo systemctl start podman-restart.service`

Copy the package from github. You can git clone or download tar.gz from github.\
`git clone https://github.com/TACC/hpcperfstats.git`
`cd hpcperfstats`

Copy the docker compose file and make the changes below:\
`cp docker-compose.yaml.example docker-compose.yaml`

Please change all the paths in the docker-compose.yml file to your paths
- Under pipeline -> volumes - change the path to the ssh keys. It doesn't have to be a user's, it just has to be a .ssh directory with valid keys and permissions.
- Under volumes -> hpcperfstatsdata -> device - change the path to your path with the correct username and directory name for your data.
- Under volumes -> hpcperfstatsnodelog -> device - change the path to your path with the correct username and directory name for your data.

You will need to create the hpcperfstatsdata and hpcperfstatslog directories, they can go anywhere on the host.\
`sudo mkdir -p /opt/hpcperfstats_data`
`sudo mkdir -p /opt/hpcperfstats_log`

 Copy the hpcperfstats.ini and make the changes below \
`cp hpcperfstats.ini.example hpcperfstats.ini`

Please change the hpcperfstats.ini file contents to your configurations
- Under [DEFAULT] -> machine - change the machine name to that of your cluster.
- Under [DEFAULT] -> host_name_ext - change it to the FQDN of your cluster.
- Under [DEFAULT] -> server - change it to the FQDN for the machine that will run this container

Copy the example supervisord.conf - this contains scripts that will do site-specific data transfer. In the example it is commenented out with instructions how to enable it.\
`cp services-conf/supervisord.conf.example services-conf/supervisord.conf`

For data transfers in or out of the container a basic script is used to rsync. There are a few examples about how we archive and get the accounting data at TACC. These commands will need to be modified for your specific HPC environment. First copy the example script over and then modify as needed.\
`cp services-conf/rsync_data.sh.example services-conf/rsync_data.sh`

In order to ingest the accounting file you can use this rsync script to rsync/scp the daily accounting file (e.x. 2018-03-03.txt) and write it to `/hpcperfstats/accounting/` in the container. 

---
### *IF YOU HAVE SSL CERTS*
`cp services-conf/nginx-withssl.conf services-conf/nginx.conf`

Then change the certificate paths in nginx.conf:
- `ssl_certificate`
- `ssl_certificate_key`

Please keep in mind that the /etc/letsencrypt is passed through in the docker-compose.yaml. If you want to use a different path you will need to update the paths in docker-compose.yaml in order to match the paths from the host to container.

### *ELSEIF YOU DONT HAVE SSL CERTS*
For quick setup you can configure the stack without SSL (This is not recommened outside of a testing environment):\
`cp services-conf/nginx-nossl.conf services-conf/nginx.conf`

---

Build and start a daemomnized container network\
`sudo docker compose up --build -d`

To see the logs\
`sudo docker compose logs`

The website should be up at this point however it will error until the accounting ingest is setup.

Useful Commands
======
 Access the PostgresSQL database\
 `docker exec -it hpcperfstats_db_1 psql -h localhost -U hpcperfstats`

 Access the Pipeline (where data is stored and processed)\
  `docker exec -it hpcperfstats_pipeline_1 su hpcperfstats`

Publications
======
[Comprehensive Resource Use Monitoring for HPC Systems with TACC Stats](http://doi.org/10.1109/HUST.2014.7)

[Understanding application and system performance through system-wide monitoring](http://doi.org/10.1109/IPDPSW.2016.145)

[![DOI](https://zenodo.org/badge/21212519.svg)](https://zenodo.org/badge/latestdoi/21212519)


Developers and Maintainers
======
Amit Ruhela  (<mailto:aruhela@tacc.utexas.edu>) <br />
Stephen Lien Harrell  (<mailto:sharrell@tacc.utexas.edu>) <br />
Sangamithra Goutham (<mailto:sgoutham@tacc.utexas.edu>) <br />
Chris Ramos (<mailto:cramos@tacc.utexas.edu>) <br />

Developer Emeritus
======
John Hammond <br />
R. Todd Evans  <br />
Bill Barth <br />
Albert Lu <br />
Junjie Li <br />
John McCalpin <br />


---------------

## Copyright
(C) 2011 University of Texas at Austin

## License

This library is free software; you can redistribute it and/or
modify it under the terms of the GNU Lesser General Public
License as published by the Free Software Foundation; either
version 2.1 of the License, or (at your option) any later version.

This library is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public
License along with this library; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
