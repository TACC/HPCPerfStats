#!/bin/bash
while true
do
    /usr/bin/rsync -avq -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' sharrell@staff.stampede3.tacc.utexas.edu:/home1/01623/sharrell/s3_acct_logs/* /hpcperfstats/accounting/
    /usr/bin/rsync -avq -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' /hpcperfstats/daily_archive/*.gz sharrell@ranch.tacc.utexas.edu:/stornext/ranch_01/ranch/projects/tacc_stats/stampede3/tgz_archive/
    /usr/bin/rsync -avq -e 'ssh -oUserKnownHostsFile=/dev/null -oStrictHostKeyChecking=no' /hpcperfstats/accounting/* sharrell@ranch.tacc.utexas.edu:/stornext/ranch_01/ranch/projects/tacc_stats/stampede3/xms-accounting
    sleep 3600
done
