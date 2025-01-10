import configparser
import os
import sys
import time

# Append your local repository path here:
# sys.path.append("/home/username/tacc_stats")

cfg = configparser.ConfigParser()

# Append your local repository path here:
cfg.read('tacc_stats.ini')

def get_db_connection_string():
    temp_string = "dbname={0} user="+cfg.get('PORTAL', 'username')+" password="+cfg.get('PORTAL', 'password')+" port="+cfg.get('PORTAL', 'port') + " host="+cfg.get('PORTAL', 'host')
    connection_string = temp_string.format(cfg.get('PORTAL', 'dbname'))
    return connection_string

def get_db_name():
    db_name = cfg.get('PORTAL', 'dbname')
    return db_name

def get_archive_dir_path():
    archive_dir_path = cfg.get('PORTAL', 'archive_dir')
    return archive_dir_path

def get_host_name_ext():
    host_name_ext = cfg.get('PORTAL', 'host_name_ext')
    return host_name_ext

def get_accounting_path():
    accounting_path = cfg.get('PORTAL', 'acct_path')
    return accounting_path

def get_daily_archive_dir_path():
    daily_archive_dir_path = cfg.get('PORTAL', 'daily_archive_dir')
    return daily_archive_dir_path
    
def get_rmq_server():
    rmq_server = cfg.get('RMQ', 'rmq_server')
    return rmq_server

def get_rmq_queue():
    rmq_queue = cfg.get('RMQ', 'rmq_queue')
    return rmq_queue

def get_machine_name():
    machine_name = cfg.get('DEFAULT', 'machine')
    return machine_name

def get_server_name():
    server_name = cfg.get('DEFAULT', 'server')
    return server_name

def get_data_dir_path():
    data_dir_path = cfg.get('DEFAULT', 'data_dir')
    return data_dir_path

def get_engine_name():
    engine_name = cfg.get('PORTAL', 'engine_name')
    return engine_name

def get_username():
    username = cfg.get('PORTAL', 'username')
    return username

def get_password():
    password = cfg.get('PORTAL', 'password')
    return password

def get_host():
    host = cfg.get('PORTAL', 'host')
    return host

def get_port():
    port = cfg.get('PORTAL', 'port')
    return port

def get_xalt_engine():
    xalt_engine = cfg.get('XALT', 'xalt_engine')
    return xalt_engine

def get_xalt_name():
    xalt_name = cfg.get('XALT', 'xalt_name')
    return xalt_name

def get_xalt_user():
    xalt_user = cfg.get('XALT', 'xalt_user')
    return xalt_user

def get_xalt_password():
    xalt_password = cfg.get('XALT', 'xalt_password')
    return xalt_password

def get_xalt_host():
    xalt_host = cfg.get('XALT', 'xalt_host')
    return xalt_host
