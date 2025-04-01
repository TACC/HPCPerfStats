#!/bin/bash
while true
do
    /usr/sbin/logrotate -c /home/hpcperfstats/services-conf/logrotate.conf
    sleep 86400
done
