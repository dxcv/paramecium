# -*- coding: utf-8 -*-
"""
Run the scheduler process.
pip install git+https://github.com/Nextdoor/ndscheduler.git#egg=ndscheduler
"""
import os
from itertools import groupby

os.environ['NDSCHEDULER_SETTINGS_MODULE'] = 'paramecium.scheduler_.settings'
from ndscheduler.server import server
from ndscheduler.corescheduler import job

from paramecium.database._postgres import create_all_table

# sometimes the logger would be duplicates, so check and keep only one.
import logging

logger = logging.getLogger()
logger.handlers = [list(g)[0] for _, g in groupby(logger.handlers, lambda x: x.__class__)]


class SimpleServer(server.SchedulerServer):

    def post_scheduler_start(self):
        create_all_table()

    # def post_scheduler_start(self):
    #     # New user experience! Make sure we have at least 1 job to demo!
    #     jobs = self.scheduler_manager.get_jobs()
    #     if len(jobs) == 0:
    #         self.scheduler_manager.add_job(
    #             job_class_string='scheduler_.jobs.sample_job.AwesomeJob',
    #             name='My Awesome Job',
    #             pub_args=['first parameter', {'second parameter': 'can be a dict'}],
    #             minute='*/1')


if __name__ == "__main__":
    SimpleServer.run()
