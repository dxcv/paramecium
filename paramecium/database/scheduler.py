# -*- coding: utf-8 -*-
"""
Run the scheduler process.
pip install git+https://github.com/Nextdoor/ndscheduler.git#egg=ndscheduler
"""
__all__ = ['SimpleServer', 'nd_job']
import logging
import os
from itertools import groupby
from uuid import uuid4

import pandas as pd

os.environ['NDSCHEDULER_SETTINGS_MODULE'] = 'paramecium.database.scheduler_config'
from ndscheduler.server import server

from ndscheduler.corescheduler import job as nd_job, job
from paramecium.database._postgres import create_all_table, upsert_data, bulk_insert, clean_duplicates

# sometimes the logger would be duplicates, so check and keep only one.
logger = logging.getLogger()
logger.handlers = [list(g)[0] for _, g in groupby(logger.handlers, lambda x: x.__class__)]


class SimpleServer(server.SchedulerServer):

    def post_scheduler_start(self):
        create_all_table()


if __name__ == "__main__":
    SimpleServer.run()


class BaseLocalizerJob(nd_job.JobBase):
    """
    Base Class for Job

    - meta_args: tuple of dict with type and description, both string.
        For example: {'type': 'string', 'description': 'name of this channel'}
    """
    meta_args = None
    meta_args_example = ''  # string, json like

    def __init__(self, job_id=None, execution_id=None):
        if job_id is None:
            job_id = uuid4()
        if execution_id is None:
            execution_id = uuid4()
        super().__init__(job_id, execution_id)

    @classmethod
    def get_model_name(cls):
        return f'{cls.__module__:s}.{cls.__name__:s}'

    @classmethod
    def meta_info(cls):
        """ 参数列表 """
        return {
            'job_class_string': cls.get_model_name(),
            'notes': cls.__doc__,
            'arguments': list(cls.meta_args) if cls.meta_args is not None else [],
            'example_arguments': cls.meta_args_example
        }

    @classmethod
    def get_logger(cls):
        logger = logging.getLogger(cls.get_model_name())
        return logger

    def insert_data(self, records, model, ukeys=None, msg=''):
        if isinstance(records, pd.DataFrame):
            records = (record.dropna().to_dict() for _, record in records.iterrows())

        if ukeys:
            self.get_logger().info(f'Upsert data {msg}...')
            return upsert_data(records, model, ukeys)
        else:
            self.get_logger().info(f'Bulk insert data {msg}...')
            return bulk_insert(records, model)

    def clean_duplicates(self, model, unique_cols):
        self.get_logger().debug(f'Clean duplicate data after bulk insert.')
        return clean_duplicates(model, unique_cols)