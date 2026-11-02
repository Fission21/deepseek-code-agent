"""Shared conservative DSH budget. Integer micro-CNY, never a provider invoice.

The parent configures an explicitly authorized pool once. Each llm/stream reserves
its full upper bound before dispatch. Unknown outcomes retain their reservation.
This is cooperative cost control, not a security boundary against local code.
"""
import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time


def integer(value, name, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**53-1:
        raise ValueError('Invalid budget '+name)
    return value


def cost(tokens, price):
    return (tokens*price+999999)//1000000


class BudgetLedger:
    def __init__(self, root):
        self.root=Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path=self.root/'budget.sqlite3'
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS pools (
                  id TEXT PRIMARY KEY, policy TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS requests (
                  id TEXT PRIMARY KEY, pool TEXT NOT NULL, owner TEXT NOT NULL,
                  reserved INTEGER NOT NULL, ceiling INTEGER NOT NULL,
                  charged INTEGER, usage TEXT, created REAL NOT NULL, policy TEXT NOT NULL);
            ''')
        os.chmod(self.path,0o600)

    @contextmanager
    def db(self, transaction=False):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        db.row_factory=sqlite3.Row
        try:
            if transaction: db.execute('BEGIN IMMEDIATE')
            yield db
            if transaction: db.commit()
        except BaseException:
            if transaction: db.rollback()
            raise
        finally: db.close()

    def configure(self, policy, default=False):
        p=dict(policy)
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',p.get('pool_id','')):
            raise ValueError('Invalid budget pool ID')
        # Other adapters need their own dispatch/retry audit before adding a route.
        if (p.get('provider'),p.get('model'))!=('deepseek-official','deepseek-flash'):
            raise ValueError('Budget route is not supported')
        for name in ('limit_micros','input_price_micros','output_price_micros','max_input_tokens','max_output_tokens'):
            integer(p.get(name),name,1)
        if p.get('attempt_bound')!=2:
            raise ValueError('Audited DeepSeek adapter requires attempt_bound=2')
        if not isinstance(p.get('expires_at'),(float,int)) or not math.isfinite(p['expires_at']) or p['expires_at']<=time.time():
            raise ValueError('Budget price policy must have a future expiry')
        if self.ceiling(p)*2>2**53-1: raise ValueError('Budget arithmetic exceeds supported range')
        with self.db(True) as db:
            if db.execute('SELECT 1 FROM pools WHERE id=?',(p['pool_id'],)).fetchone():
                raise ValueError('Budget pool already exists; refusing to reset spend')
            db.execute('INSERT INTO pools VALUES (?,?,?)',(p['pool_id'],json.dumps(p),p['expires_at']))
            if default: db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('default_pool',p['pool_id']))
        return self.snapshot(p['pool_id'])

    @staticmethod
    def ceiling(p):
        return cost(p['max_input_tokens'],p['input_price_micros'])+cost(p['max_output_tokens'],p['output_price_micros'])

    def refresh_prices(self,pool_id,input_price_micros,output_price_micros,expires_at):
        integer(input_price_micros,'input_price_micros',1);integer(output_price_micros,'output_price_micros',1)
        if not isinstance(expires_at,(float,int)) or not math.isfinite(expires_at) or expires_at<=time.time():
            raise ValueError('Price refresh requires a future expiry')
        with self.db(True) as db:
            row=db.execute('SELECT policy FROM pools WHERE id=?',(pool_id,)).fetchone()
            if not row:raise ValueError('Unknown budget pool')
            p=json.loads(row[0]);p.update(input_price_micros=input_price_micros,output_price_micros=output_price_micros,expires_at=expires_at)
            if self.ceiling(p)*2>2**53-1:raise ValueError('Budget arithmetic exceeds supported range')
            db.execute('UPDATE pools SET policy=?,expires_at=? WHERE id=?',(json.dumps(p),expires_at,pool_id))
        return self.snapshot(pool_id)

    def binding(self, pool_id=None):
        with self.db() as db:
            if pool_id is None:
                row=db.execute("SELECT value FROM settings WHERE key='default_pool'").fetchone()
                pool_id=row[0] if row else None
            self.policy(db,pool_id)
        return {'root':str(self.root),'pool_id':pool_id}

    @staticmethod
    def policy(db,pool_id):
        row=db.execute('SELECT * FROM pools WHERE id=?',(pool_id,)).fetchone()
        if not row: raise ValueError('No authorized DSH budget configured; configure a shared pool before execution')
        if row['expires_at']<=time.time(): raise ValueError('DSH budget price policy expired')
        return json.loads(row['policy'])

    @staticmethod
    def totals(db,pool_id):
        row=db.execute('SELECT COALESCE(SUM(CASE WHEN charged IS NULL THEN reserved ELSE 0 END),0), COALESCE(SUM(charged),0) FROM requests WHERE pool=?',(pool_id,)).fetchone()
        return row[0],row[1]

    def snapshot(self,pool_id):
        with self.db() as db:
            row=db.execute('SELECT * FROM pools WHERE id=?',(pool_id,)).fetchone()
            if not row: raise ValueError('Unknown budget pool')
            p=json.loads(row['policy']);held,spent=self.totals(db,pool_id)
            return {'pool_id':pool_id,'limit_micros':p['limit_micros'],'reserved_micros':held,
                    'charged_micros':spent,'available_micros':p['limit_micros']-held-spent,
                    'expired':row['expires_at']<=time.time()}

    def admit(self,pool_id):
        with self.db() as db:
            p=self.policy(db,pool_id);held,spent=self.totals(db,pool_id)
            if held+spent+self.ceiling(p)*p['attempt_bound']>p['limit_micros']:
                raise ValueError('DSH budget exhausted: no room for the next request upper bound')

    def reserve(self,pool_id,request_id,owner,provider,model,input_tokens,output_tokens,opaque_retry_possible=True):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=200 or not isinstance(owner,str):
            raise ValueError('Invalid budget request identity')
        integer(input_tokens,'input_tokens',1);integer(output_tokens,'output_tokens',1)
        if type(opaque_retry_possible) is not bool:raise ValueError('Invalid retry bound')
        with self.db(True) as db:
            p=self.policy(db,pool_id)
            if (provider,model)!=(p['provider'],p['model']): raise ValueError('Budget route mismatch')
            if input_tokens>p['max_input_tokens'] or output_tokens>p['max_output_tokens']:
                raise ValueError('Model request exceeds budget token bounds')
            if db.execute('SELECT 1 FROM requests WHERE id=?',(request_id,)).fetchone():
                raise ValueError('Request already reserved; replay must not dispatch')
            ceiling=self.ceiling(p);amount=ceiling*(p['attempt_bound'] if opaque_retry_possible else 1)
            held,spent=self.totals(db,pool_id)
            if held+spent+amount>p['limit_micros']: raise ValueError('DSH budget exhausted')
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,NULL,NULL,?,?)',(request_id,pool_id,owner,amount,ceiling,time.time(),json.dumps(p)))
        return {'request_id':request_id,'reserved_micros':amount}

    def settle(self,request_id,usage):
        if not isinstance(usage,dict): raise ValueError('Missing usage; reservation retained')
        # DSH reports cache tokens separately. Reasoning is already in outputTokens.
        inp=integer(usage.get('inputTokens'),'inputTokens')
        out=integer(usage.get('outputTokens'),'outputTokens')
        inp+=integer(usage.get('cacheReadTokens',0),'cacheReadTokens')+integer(usage.get('cacheWriteTokens',0),'cacheWriteTokens')
        normalized=json.dumps({'input':inp,'output':out},sort_keys=True)
        with self.db(True) as db:
            r=db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
            if not r: raise ValueError('Unknown budget reservation')
            p=json.loads(r['policy'])
            if inp>p['max_input_tokens'] or out>p['max_output_tokens']: raise ValueError('Usage exceeds reserved bounds; reservation retained')
            if r['charged'] is not None:
                if normalized!=r['usage']: raise ValueError('Conflicting settlement')
                return {'request_id':request_id,'charged_micros':r['charged']}
            # The adapter may have one opaque retry. Retain that entire ceiling;
            # successful stream usage only establishes the last attempt's cost.
            charged=cost(inp,p['input_price_micros'])+cost(out,p['output_price_micros'])+r['reserved']-r['ceiling']
            db.execute('UPDATE requests SET charged=?,usage=? WHERE id=?',(charged,normalized,request_id))
        return {'request_id':request_id,'charged_micros':charged}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('operation',choices=['configure','snapshot','reserve','settle','refresh_prices'])
    a=parser.parse_args();params=json.load(sys.stdin)
    try: result=getattr(BudgetLedger(a.root),a.operation)(**params)
    except (ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'error':str(exc)}));return 1
    print(json.dumps(result));return 0


if __name__=='__main__': sys.exit(main())
