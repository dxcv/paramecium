# -*- coding: utf-8 -*-
"""
@Time: 2020/5/12 20:58
@Author: Sue Zhu
"""
import json

import numpy as np
import pandas as pd
from requests import request

from paramecium.database import fund_org, create_all_table
from paramecium.utils import chunk
from paramecium.scheduler_.jobs._localizer import TushareCrawlerJob, WebCrawlerJob


class FundDescription(TushareCrawlerJob):
    """
    Crawler fund descriptions from tushare
    """
    meta_args = (
        # pre_truncate:
        {'type': 'int', 'description': '0 or 1 as bool, default `1`'},
    )
    meta_args_example = '[1]'

    def run(self, pre_truncate=0, *args, **kwargs):
        model = fund_org.MutualFundDescription

        if pre_truncate:
            with self.get_session() as session:
                self.get_logger().info(f'truncate table {model.__tablename__:s}.')
                session.execute(f'truncate table {model.__tablename__:s};')

        self.get_logger().info('Getting description from tushare')
        fund_info = pd.concat(
            (self.get_tushare_data(api_name='fund_basic', market=m) for m in list('OE')),
            axis=0, sort=False
        ).fillna(np.nan).rename(columns={
            'ts_code': 'wind_code',
            'name': 'short_name',
            'fund_type': 'invest_type',
            'found_date': 'setup_date',
            'due_date': 'maturity_date',
            'purc_startdate': 'purchase_start_dt',
            'redm_startdate': 'redemption_start_dt',
            'invest_type': 'invest_style',
            'type': 'fund_type',
        }).filter(model.__dict__.keys(), axis=1)

        for c in (
                'setup_date', 'maturity_date', 'issue_date',
                'list_date', 'delist_date',
                'purchase_start_dt', 'redemption_start_dt',
        ):
            fund_info.loc[:, c] = pd.to_datetime(fund_info[c])

        fund_info.loc[
            lambda df: df['setup_date'].notnull() & df['maturity_date'].isnull(), 'maturity_date'] = pd.Timestamp.max

        self.get_logger().info('Saving description into database')
        self.upsert_data(records=fund_info, model=model, ukeys=[model.wind_code])


class FundSales(WebCrawlerJob):
    meta_args = (
        # n_records
        {'type': 'int', 'description': 'number more than sale funds.'},
    )

    def run(self, n_records=3000, *args, **kwargs):
        respons_raw = request(
            "GET",
            f"https://www.xcsc.com/servlet/json?funcNo=742100&curtPageNo=1&numPerPage={n_records}&isDxsale=0"
        )
        respons_json = json.loads(respons_raw.text)['results'][0]
        if respons_json['totalPages'] > 1:
            self.get_logger().warn(
                f"the results not cover all funds for sale, please make "
                f"`n_records` more than {respons_json['totalRows']}.")
        respons_df = pd.DataFrame(respons_json['data']).reindex(columns=[
            'product_code', 'product_id', 'product_abbr', 'risk_level', 'per_buy_limit', 'product_status',
            'subscribe_start_time', 'subscribe_end_time', 'purchase_rates', 'purchase_rates_dis',
        ]).replace('', np.nan)
        for c in ('product_id', 'risk_level', 'per_buy_limit', 'product_status',
                  'purchase_rates', 'purchase_rates_dis'):
            respons_df.loc[:, c] = pd.to_numeric(respons_df[c], errors='coerce')
        for c in ('subscribe_start_time', 'subscribe_end_time'):
            respons_df.loc[:, c] = pd.to_datetime(respons_df[c].replace('0', np.nan), format='%Y%m%d')
        model = fund_org.MutualFundSale
        self.upsert_data(respons_df.astype({
            'product_id': int, 'risk_level': int,
            'per_buy_limit': float, 'product_status': int,
            'purchase_rates': float, 'purchase_rates_dis': float,
        }), model=model, ukeys=[model.product_code])


class FundNav(TushareCrawlerJob):
    """
    Crawler fund net asset values from tushare
    Since tushare api can only request 10,000 times per hour,
    use `try-except` to get most.
    """

    def run(self, *args, **kwargs):
        self.get_logger().info('query exist nav data to get query range')
        with self.get_session() as session:
            max_dts = pd.read_sql(
                """
                select t.wind_code, t.max_dt
                from (
                    select 
                        d.*,
                        case when p.max_dt is null then d.setup_date else p.max_dt end as max_dt
                    from mf_org_description d 
                    left join (select wind_code, max(trade_dt) as max_dt from mf_org_nav group by wind_code) p 
                    on d.wind_code=p.wind_code
                ) t             
                where 
                    t.max_dt < t.maturity_date
                    and t.setup_date > date('1900-01-01')
                """,
                session.bind, parse_dates=['max_dt'], index_col=['wind_code']
            ).squeeze().loc[lambda ser: ser.index.str.len() < 10]
        max_dts -= pd.Timedelta(days=7)

        nav = pd.DataFrame()
        for i, (code, dt) in enumerate(max_dts.items(), start=1):
            try:
                self.get_logger().info(f'getting {code} nav from tushare')
                nav = pd.concat((
                    nav,
                    self.get_tushare_data(
                        api_name='fund_nav', ts_code=code,
                        date_cols=['end_date', 'ann_date'],
                        col_mapping={
                            'ts_code': 'wind_code',
                            'end_date': 'trade_dt',
                            'accum_nav': 'acc_nav',
                        },
                    ).loc[lambda df: df['trade_dt'] >= dt]
                ), axis=0)
            except Exception as e:
                self.get_logger().error(f'error happends when run {repr(e)}')
                break

            if nav.shape[0] > 10000:
                nav = self.insert_nav(nav, i / max_dts.shape[0])

        self.insert_nav(nav, 1)
        self.clean_duplicates(
            fund_org.MutualFundNav,
            [fund_org.MutualFundNav.wind_code, fund_org.MutualFundNav.trade_dt]
        )

    def insert_nav(self, nav, pct):
        nav['adj_factor'] = nav['adj_nav'].div(nav['unit_nav']).round(6)
        self.bulk_insert(
            records=nav.drop(['accum_div', 'adj_nav'], axis=1, errors='ignore'),
            model=fund_org.MutualFundNav,
            msg=f'fund navs({pct * 100:.2f}%)'
        )
        return pd.DataFrame()


class FundManager(TushareCrawlerJob):
    """
    Crawler fund net asset values from tushare
    """
    _COL = {
        'ts_code': 'wind_code',
        'ann_date': 'ann_date',
        'name': 'manager_name',
        'begin_date': 'start_dt',
        'end_date': 'end_dt',
    }

    def run(self, *args, **kwargs):
        model = fund_org.MutualFundManager

        with self.get_session() as session:
            fund_list = pd.DataFrame(session.query(fund_org.MutualFundDescription.wind_code)).squeeze()

        for funds in chunk(fund_list, 100):
            managers = self.get_tushare_data(
                api_name='fund_manager',
                date_cols=['ann_date', 'begin_date', 'end_date'],
                fields=self._COL.keys(),
                col_mapping=self._COL,
                ts_code=','.join(funds)
            ).fillna({
                'start_dt': pd.Timestamp.min,
                'end_dt': pd.Timestamp.max,
            })
            self.upsert_data(managers, model, ukeys=[model.wind_code, model.start_dt, model.manager_name])


if __name__ == '__main__':
    create_all_table()
    FundDescription().run()
    FundSales().run()
    FundNav(env='tushare_prod').run()
    FundManager().run()