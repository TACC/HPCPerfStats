#!/usr/bin/env python
import pika
import os, sys
import time
import cfg

stats_dir = "/hpc/tacc_stats_site/stampede/archive"

def on_message(channel, method_frame, header_frame, body):
    if body[0] == '$': host = body.split('\n')[1].split()[1]       
    else: host = body.split()[2]

    print host
    print body
    host_dir = os.path.join(cfg.archive_dir, host)
    if not os.path.exists(host_dir):
        os.makedirs(host_dir)
    
    current_path = os.path.join(host_dir, "current")
    if body[0] == '$':
        if os.path.exists(current_path):
            os.unlink(current_path)

        with open(current_path, 'w') as fd:
            link_path = os.path.join(host_dir, str(int(time.time())))
            if not os.path.exists(link_path):
                os.link(current_path, link_path)

    with open(current_path, 'a') as fd:
        fd.write(body)

    channel.basic_ack(delivery_tag=method_frame.delivery_tag)

connection = pika.BlockingConnection()
channel = connection.channel()
channel.basic_consume(on_message, sys.argv[1])
try:
    channel.start_consuming()
except KeyboardInterrupt:
    channel.stop_consuming()
connection.close()
