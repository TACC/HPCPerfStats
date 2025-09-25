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
VERSION = "2.4"

DESCRIPTION = ("A performance monitoring and analysis package for \
High Performance Computing Platforms")
LONG_DESCRIPTION = """
HPCPerfStats unifies and extends the measurements taken by Linux monitoring utilities such as systat/SAR, iostat, etc.~and resolves measurements by job and hardware device so that individual job/applications can be analyzed separately.  It also provides a set of analysis and reporting tools which analyze resource use data and flags jobs/applications with low resource use efficiency.
"""

scripts=[
    'hpcperfstats/analysis/metrics/update_metrics.py',
    'hpcperfstats/site/manage.py',
    'hpcperfstats/dbload/sacct_gen.py',
    'hpcperfstats/dbload/sync_acct.py',
    'hpcperfstats/dbload/sync_timedb.py',
    'hpcperfstats/dbload/sync_timedb_archive.py',
    'hpcperfstats/listend.py'
    ]

config = ConfigParser()
config.read("hpcperfstats.ini")

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
    install_requires = ['numpy', 'psycopg2', 'pgcopy',
                        'pandas', 'bokeh', 'django', 'python-hostlist', 
                        'pika', 'configparser', 'mysqlclient',
                        'gunicorn', 'cryptography', 'requests',
                        'requests-toolbelt','legacy-cgi'],
    platforms = 'any',
    classifiers = [
        'Development Status :: 5 - Production',
        'Environment :: Console',
        'Operating System :: Linux',
        'Intended Audience :: Science/Research',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.12',
        'Topic :: Scientific/Engineering',
    ]
)
