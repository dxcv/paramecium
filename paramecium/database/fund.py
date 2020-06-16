# -*- coding: utf-8 -*-
"""
@Time: 2020/6/7 10:52
@Author: Sue Zhu
"""
from functools import lru_cache

import pandas as pd

from ._postgres import get_session
from .comment import get_sector, get_dates
from .pg_models import fund
from .. import const
from ..interface import AbstractUniverse


@lru_cache()
class FundUniverse(AbstractUniverse):

    def __init__(
            self, include_=None,
            # 定期开放,委外,机构,可转债
            exclude_=("1000007793000000", "1000027426000000", "1000031885000000", "1000023509000000"),
            initial_only=True, open_only=True, issue=250, size_=0.5
    ):
        self.include = include_
        self.exclude = exclude_
        self.initial_only = initial_only
        self.open_only = open_only
        self.issue = issue  # trade days
        self.size = size_  # TODO: size limit has not been apply.

    @lru_cache(maxsize=2)
    def get_instruments(self, month_end):
        issue_dt = [t for t in get_dates(const.FreqEnum.D) if t < month_end][-self.issue]
        with get_session() as ss:
            filters = [
                # date
                fund.Description.setup_date <= issue_dt,
                fund.Description.redemption_start_dt <= month_end,
                fund.Description.maturity_date >= month_end,
                # not connect fund
                fund.Description.wind_code.notin_(ss.query(fund.Connections.child_code))
            ]
            if self.open_only:
                filters.append(fund.Description.fund_type == '契约型开放式')
            if self.initial_only:
                filters.append(fund.Description.is_initial == 1)

            fund_list = {code for (code,) in ss.query(fund.Description.wind_code).filter(*filters).all()}

        if self.include or self.exclude:
            sector_type = pd.concat((
                get_sector(const.AssetEnum.CMF, valid_dt=month_end, sector_prefix='2001'),
                get_sector(
                    const.AssetEnum.CMF, sector_prefix='1000',
                    valid_dt=max((t for t in get_dates(const.FreqEnum.Q) if t < month_end)),
                ),
            ))
            if self.include:
                in_fund = sector_type.loc[lambda df: df['sector_code'].isin(self.include), 'wind_code']
                fund_list = fund_list & {*in_fund}
            if self.exclude:
                ex_fund = sector_type.loc[lambda df: df['sector_code'].isin(self.exclude), 'wind_code']
                fund_list = fund_list - {*ex_fund}

        return fund_list


def get_convert_fund(valid_dt):
    with get_session() as ss:
        query = pd.DataFrame(
            ss.query(
                fund.Converted.wind_code,
                fund.Converted.chg_date,
                fund.Converted.ann_date,
                fund.Converted.memo
            ).filter(fund.Converted.chg_date <= valid_dt).all()
        )
    return query
