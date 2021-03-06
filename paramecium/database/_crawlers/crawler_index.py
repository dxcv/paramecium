# -*- coding: utf-8 -*-
"""
@Time: 2020/6/4 9:06
@Author: Sue Zhu
"""
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.orm import Query

from ._base import *
from ..comment import get_last_td
from ..pg_models import index
from ...utils import chunk


class IndexDescription(CrawlerJob):
    """
    Crawling index description
        1. from tushare, mainly a share index
        http://tushare.xcsc.com:7173/document/2?doc_id=94
        2. wind api
        bond and fund index not available from tushare, conf key `crawler_index_desc`
    """
    TS_ENV = 'dev'

    def run(self, *args, **kwargs):
        types = get_type_codes('index_org_description')

        self.get_logger().info('Localized from tushare api')
        types['publisher'].pop('WIND')
        for idx_type in types['index_code'].keys():
            self.get_and_insert_ts(market='WIND', category=idx_type)
        for pub in types['publisher'].keys():
            self.get_and_insert_ts(market=pub)

        fund_index = self.get_tushare_data(api_name='index_description').assign(index_code='647006000', localized=1)
        fund_index = fund_index.filter(index.Description.__dict__.keys(), axis=1)
        self.insert_data(fund_index, index.Description, index.Description.get_primary_key())

        self.get_logger().info('Localized from wind api')
        w_conf = get_wind_conf('crawler_index_desc')
        chunk_size = 8000 // len(w_conf['fields'].keys())  # wss限8000单元格
        conf_codes = {c: tp for tp, c_list in get_wind_conf('crawler_index').items() for c in c_list}
        for codes in chunk(conf_codes.keys(), chunk_size):
            desc = self.query_wind(
                api_name='wss', codes=codes, fields=w_conf['fields'].keys(),
                col_mapping=w_conf['fields'], dt_cols=w_conf['dt_cols']
            ).assign(localized=-1)
            desc = desc.assign(localized=-1, wind_code=desc.index, index_code=desc.index.map(conf_codes))
            self.insert_data(records=desc, model=index.Description, ukeys=index.Description.get_primary_key())

    def get_and_insert_ts(self, **func_kwargs):
        data = self.get_tushare_data(
            api_name='index_basic', **func_kwargs
        ).filter(index.Description.__dict__.keys(), axis=1)
        data.loc[:, 'index_code'] = data['index_code'].dropna().map(lambda x: f'{x:.0f}')
        data = data.fillna({'expire_date': pd.Timestamp.max})
        self.insert_data(data, index.Description, index.Description.get_primary_key())


class IndexPrice(CrawlerJob):
    """
    Crawling index description from tushare
    http://tushare.xcsc.com:7173/document/2?doc_id=95
    """
    ts_limit = 2999
    meta_args = (
        {'type': 'int', 'description': 'if localize from tushare'},  # ts
        {'type': 'int', 'description': 'if check new code from tushare'},  # ts
    )
    meta_args_example = '[1, 1]'

    def run(self, ts=1, check_new=1, *args, **kwargs):
        self.get_logger().debug('localize wind api')
        with get_session() as ss:
            price_group = Query([
                index.EODPrice.wind_code,
                sa.func.max(index.EODPrice.trade_dt).label('max_dt')
            ]).group_by(
                index.EODPrice.wind_code
            ).subquery('g')
            desc = pd.DataFrame(
                ss.query(
                    index.Description.wind_code,
                    index.Description.base_date,
                    price_group.c.max_dt,
                    index.Description.localized,
                    index.Description.index_code
                ).join(
                    price_group,
                    index.Description.wind_code == price_group.c.wind_code,
                    isouter=True  # left join
                ).filter(
                    index.Description.expire_date > sa.func.current_date(),
                ).all()
            ).set_index('wind_code')
            desc['max_dt'] = desc.loc[:, ['base_date', 'max_dt']].apply(pd.to_datetime).max(axis=1)
            desc = desc.loc[lambda df: df['max_dt'] < get_last_td()]

        if ts:
            self.get_logger().debug('localize listed ts code.')
            # for code, start in desc.loc[desc['localized'].eq(1), 'max_dt'].items():
            for code, row in desc.iterrows():
                try:
                    c_start = row['max_dt'] - pd.Timedelta(days=5)
                    api_name = 'index_basic' if row['index_code'] != '647006000' else 'index_description'
                    for dt in pd.date_range(c_start, pd.Timestamp.now(), freq=f'{self.ts_limit}D'):
                        self.localized_ts(ts_code=code, start_date=dt, api=api_name)
                except Exception as e:
                    self.get_logger().error(repr(e))
                    break

        if check_new:
            self.get_logger().debug('check new code.')
            has_price, no_price = {}, []
            idx_range = ["647000000", "647002000", "647002001", "647002002", "647002003", "647002004",  # "行业指数",
                         "647003000", "647004000", "647004001", "647004002", "647005000", "647001000", ]
            for code, base_dt in desc.loc[
                desc['index_code'].isin(idx_range) & desc['localized'].eq(0), 'max_dt'].items():
                try:
                    for dt in pd.date_range(base_dt, pd.Timestamp.now(), freq=f'{self.ts_limit}D'):
                        ts_price = self.localized_ts(ts_code=code, start_date=dt)
                        if ts_price.empty:
                            no_price.append(code)
                            break
                    else:
                        has_price[code] = ts_price['currency'].iloc[0]
                except Exception as e:
                    self.get_logger().error(repr(e))
                    break

            with get_session() as ss:
                ss.query(index.Description).filter(
                    index.Description.wind_code.in_(has_price)
                ).update(dict(localized=1, updated_at=sa.func.current_timestamp()), synchronize_session='fetch')
                ss.query(index.Description).filter(
                    index.Description.wind_code.in_(no_price)
                ).update(dict(localized=-1, updated_at=sa.func.current_timestamp()), synchronize_session='fetch')

        self.get_logger().debug('localize wind api')
        codes = [c for _, c_list in get_wind_conf('crawler_index').items() for c in c_list]
        w_conf = {k.upper(): v for k, v in get_wind_conf('crawler_index_price')['fields'].items()}
        for code, start in desc.filter(codes, axis=0)['max_dt'].items():
            try:
                for dt in pd.date_range(start - pd.Timedelta(days=5), pd.Timestamp.now(), freq='1000D'):
                    # wsd限8000单元格
                    price = self.query_wind(
                        api_name='wsd', codes=code, fields=w_conf.keys(),
                        beginTim=dt, endTime=max((dt + pd.Timedelta(days=1000), get_last_td())),
                        options="", col_mapping=w_conf
                    ).dropna(subset=['close_'])
                    self.insert_data(
                        price.assign(wind_code=code, trade_dt=pd.to_datetime(price.index)),
                        index.EODPrice, msg=f'{code} - {dt:%Y%m%d}'
                    )
            except Exception as e:
                self.get_logger().error(f"Error happened {e!r}")
                break

        self.clean_duplicates(index.EODPrice, [index.EODPrice.wind_code, index.EODPrice.trade_dt])

    def localized_ts(self, ts_code, start_date, api='index_daily'):
        data = self.get_tushare_data(
            api_name=api,
            ts_code=ts_code, start_date=start_date.strftime('%Y%m%d'),
            end_date=(start_date + pd.Timedelta(days=self.ts_limit)).strftime('%Y%m%d')
        ).filter([*index.EODPrice.__dict__.keys(), 'currency'], axis=1)
        self.insert_data(records=data.drop('currency', axis=1, errors='ignore'), model=index.EODPrice)
        return data
