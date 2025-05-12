#!/bin/bash
set -x
while true
do
    /usr/bin/rsync -avq -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' sharrell@staff.stampede3.tacc.utexas.edu:/home1/01623/sharrell/s3_acct_logs/* /hpcperfstats/accounting/
    /usr/bin/rsync -avpq --chmod=Du=rwx,Dg=rx,Do=rx,Fu=rw,Fg=r,Fo=r -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' /hpcperfstats/daily_archive/*.gz sharrell@ranch.tacc.utexas.edu:/stornext/ranch_01/ranch/projects/tacc_stats/stampede3/tgz_archive/
    /usr/bin/rsync -avpq --chmod=Du=rwx,Dg=rx,Do=rx,Fu=rw,Fg=r,Fo=r -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' /hpcperfstats/accounting/* sharrell@ranch.tacc.utexas.edu:/stornext/ranch_01/ranch/projects/tacc_stats/stampede3/xms-accounting
    /usr/bin/rsync -avpq -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' /hpcperfstatslog/*.gz sharrell@ranch.tacc.utexas.edu:/stornext/ranch_01/ranch/projects/tacc_stats/stampede3/node_logs/
    # Wait 12 hours, then do it again!
    sleep 43200
done
