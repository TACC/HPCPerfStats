#!/bin/bash
set -x
while true
do
# The first command copies the accounting files into the container.
# The second command moves all archived node-level data to a tape archive.
    /usr/bin/rsync -avq -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' hpcperf@staff.stampede3.tacc.utexas.edu:/home1/01623/sharrell/s3_acct_logs/* /hpcperfstats/accounting/
    /usr/bin/rsync -avpq --chmod=Du=rwx,Dg=rx,Do=rx,Fu=rw,Fg=r,Fo=r -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' /hpcperfstats/daily_archive/*.gz hpcperf@ranch.tacc.utexas.edu:/stornext/ranch_01/ranch/projects/tacc_stats/stampede3/tgz_archive/
  

    sleep 43200
done
