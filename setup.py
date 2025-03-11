#!/usr/bin/env python
from setuptools import setup, find_packages
import os
from configparser import ConfigParser

DISTNAME = 'hpcperfstats'
LICENSE = 'LGPL'
AUTHOR = "Texas Advanced Computing Center"
EMAIL = "sharrell@tacc.utexas.edu"
URL = "http://www.tacc.utexas.edu"
DOWNLOAD_URL = 'https://github.com/TACC/hpcperfstats'
VERSION = "2.3.5"

DESCRIPTION = ("A performance monitoring and analysis package for \
High Performance Computing Platforms")
LONG_DESCRIPTION = """
TACC Stats unifies and extends the measurements taken by Linux monitoring utilities such as systat/SAR, iostat, etc.~and resolves measurements by job and hardware device so that individual job/applications can be analyzed separately.  It also provides a set of analysis and reporting tools which analyze TACC Stats resource use data and flags jobs/applications with low resource use efficiency.
"""

scripts=[
    'hpcperfstats/analysis/metrics/update_metrics.py',
    'hpcperfstats/site/manage.py',
    'hpcperfstats/dbload/sacct_gen.py',
    'hpcperfstats/dbload/sync_acct.py',
    'hpcperfstats/dbload/sync_timedb.py',
    'hpcperfstats/listend.py'
    ]

config = ConfigParser()
config.read("hpcperfstats.ini")

#with open("hpcperfstats/cfg.py", 'w') as fd:
#    for s in config.sections():
#        for key, val in dict(config.items(s)).items():
#            fd.write(key + " = " + "\"" + val + "\"" + '\n')

setup(
    name = DISTNAME,
    version = VERSION,
    maintainer = AUTHOR,
    maintainer_email = EMAIL,
    description = DESCRIPTION,
    license = LICENSE,
    url = URL,
    download_url = DOWNLOAD_URL,
    long_description = LONG_DESCRIPTION,
    packages = find_packages(),
    package_data = {'hpcperfstats' : ['cfg.py']},
    include_package_data = True,
    scripts = scripts,
    install_requires = ['argparse','numpy', 'psycopg2-binary', 'pandas', 'pgcopy',
                        'bokeh', 'django==4.2.17', 'python-hostlist', 'PyMySQL',
                        'mysql-connector-python', 'python-memcached', 'pika', 'mysqlclient'],
    platforms = 'any',
    classifiers = [
        'Development Status :: 5 - Production',
        'Environment :: Console',
        'Operating System :: Linux',
        'Intended Audience :: Science/Research',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Topic :: Scientific/Engineering',
    ]
)
