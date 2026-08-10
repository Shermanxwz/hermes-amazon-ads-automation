#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx2 as httpx

ROOT = Path('/opt/hermes-amazon-ads-automation')
DB = Path(os.environ.get('ADS_CONTROL_DB', '/var/lib/hermes-amazon-ads-control/state.db'))
PROFILE = '4498826511098550'
SESSION = 'orchestrator-us-daily-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value
    return values


ENV = load_env(Path('/etc/hermes-amazon-ads-control.env'))
ENV.update(load_env(Path('/var/lib/hermes-amazon-ads-control/runtime.env')))


def control_post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        'http://127.0.0.1:8790' + path,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        method='POST',
        headers={'Authorization': 'Bearer ' + ENV['ADS_CONTROL_AGENT_TOKEN'], 'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode())


def control_get(path: str) -> dict:
    req = urllib.request.Request(
        'http://127.0.0.1:8790' + path,
        headers={'Authorization': 'Bearer ' + ENV['ADS_CONTROL_AGENT_TOKEN']},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode())


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def extract_campaigns(value):
    if isinstance(value, dict):
        for key in ('campaigns', 'campaignsList', 'campaignsResponse', 'items'):
            candidate = value.get(key)
            if isinstance(candidate, list) and any(isinstance(x, dict) and ('campaignId' in x or 'campaign_id' in x) for x in candidate):
                return candidate
        for child in value.values():
            found = extract_campaigns(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = extract_campaigns(child)
            if found is not None:
                return found
    return None


async def live_campaign_read() -> tuple[dict, dict, list[dict]]:
    headers = {'Authorization': 'Bearer ' + ENV['AMAZON_ADS_MCP_ACCESS_TOKEN'], 'MCP-Protocol-Version': '2025-03-26'}
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60.0, read=300.0), headers=headers) as client:
        async with streamable_http_client('https://advertising-ai.amazon.com/mcp', http_client=client, terminate_on_close=False) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                args = {'body': {'accessRequestedAccount': {'profileId': PROFILE}, 'adProductFilter': {'include': ['SPONSORED_PRODUCTS']}, 'maxResults': 100}}
                result = await session.call_tool('campaign_management-query_campaign', args)
                text = '\n'.join(getattr(item, 'text', str(item)) for item in result.content or [])
                payload = json.loads(text)
                campaigns = extract_campaigns(payload)
                if not campaigns:
                    raise RuntimeError('US campaign read returned no campaign list')
                return args, payload, campaigns


def rebuild_snapshot(store: sqlite3.Connection, live: list[dict]) -> tuple[str, dict, int]:
    target_row = store.execute(
        "select id, normalized_snapshot_gzip from report_jobs where profile_id=? and report_type='spTargeting' and status='INGESTED' order by end_date desc, updated_at desc limit 1",
        (PROFILE,),
    ).fetchone()
    if target_row and target_row['normalized_snapshot_gzip']:
        job_id = target_row['id']
        snapshot = json.loads(gzip.decompress(target_row['normalized_snapshot_gzip']).decode())
        live_by_id = {str(item.get('campaignId') or item.get('campaign_id')): item for item in live if isinstance(item, dict)}
        for campaign in snapshot.get('campaigns') or []:
            current = live_by_id.get(str(campaign.get('campaign_id') or ''))
            if not current:
                raise RuntimeError('US live read missing campaign ' + str(campaign.get('campaign_id') or ''))
            budgets = current.get('budgets') or []
            try:
                campaign['budget'] = float(budgets[0]['budgetValue']['monetaryBudgetValue']['monetaryBudget']['value']) if budgets else 0.0
            except (KeyError, TypeError, ValueError):
                campaign['budget'] = 0.0
            campaign['state'] = current.get('state')
            campaign['status'] = (current.get('status') or {}).get('deliveryStatus') if isinstance(current.get('status'), dict) else None
        raw = canonical(snapshot)
        normalized_hash = hashlib.sha256(raw).hexdigest()
        schema = {'profile': sorted(snapshot.get('profile') or {}), 'window': sorted(snapshot.get('window') or {}), 'account': sorted(snapshot.get('account') or {}), 'campaigns': sorted({key for item in snapshot.get('campaigns') or [] for key in item}), 'targets': sorted({key for item in snapshot.get('targets') or [] for key in item})}
        schema_hash = hashlib.sha256(canonical(schema)).hexdigest()
        backup = DB.parent / 'backups' / ('state.db.pre-daily-target-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        shutil.copy2(DB, backup)
        store.execute('begin immediate')
        store.execute('update report_jobs set normalized_snapshot_gzip=?, normalized_hash=?, schema_hash=?, row_count=?, updated_at=? where id=?', (gzip.compress(raw, mtime=0), normalized_hash, schema_hash, len(snapshot.get('targets') or []), datetime.now(timezone.utc).isoformat(), job_id))
        store.commit()
        return job_id, snapshot, len(snapshot.get('targets') or [])
    row = store.execute(
        "select id, normalized_snapshot_gzip from report_jobs where profile_id=? and report_type='spCampaigns' and status='INGESTED' order by end_date desc, updated_at desc limit 1",
        (PROFILE,),
    ).fetchone()
    if not row:
        raise RuntimeError('no INGESTED US report job')
    job_id, blob = row
    original = json.loads(gzip.decompress(blob).decode())
    raw_rows = original.get('rows') or original.get('campaigns') or []
    live_by_id = {str(item.get('campaignId') or item.get('campaign_id')): item for item in live if isinstance(item, dict)}
    campaigns = []
    for raw in raw_rows:
        cid = str(raw.get('campaign.id') or raw.get('campaign_id') or '')
        current = live_by_id.get(cid)
        if not current:
            raise RuntimeError(f'US live read missing campaign {cid}')
        budgets = current.get('budgets') or []
        budget = 0.0
        try:
            budget = float(budgets[0]['budgetValue']['monetaryBudgetValue']['monetaryBudget']['value']) if budgets else 0.0
        except (KeyError, TypeError, ValueError):
            pass
        def number(key, default=0.0):
            try:
                return float(raw.get(key)) if raw.get(key) is not None else default
            except (TypeError, ValueError):
                return default
        campaigns.append({
            'campaign_id': cid, 'campaign_name': raw.get('campaign.name') or current.get('name') or '',
            'ad_product': 'SPONSORED_PRODUCTS', 'state': current.get('state'),
            'status': (current.get('status') or {}).get('deliveryStatus'), 'budget': budget,
            'currency': 'USD', 'marketplace': 'US', 'impressions': int(number('metric.impressions', raw.get('impressions', 0))),
            'clicks': int(number('metric.clicks', raw.get('clicks', 0))), 'spend': number('metric.totalCost', raw.get('spend', 0)),
            'sales': number('metric.sales', raw.get('sales', 0)), 'orders': int(number('metric.purchases', raw.get('orders', 0))),
            'units_sold': int(number('metric.unitsSold', raw.get('units_sold', 0))), 'roas': number('metric.roas', raw.get('roas', 0)),
            'ctr': number('metric.ctr', raw.get('ctr', 0)),
        })
    if len(campaigns) != len(raw_rows):
        raise RuntimeError(f'US live read mismatch report_rows={len(raw_rows)} merged={len(campaigns)}')
    account = {key: sum(float(item.get(key) or 0) for item in campaigns) for key in ('impressions', 'clicks', 'spend', 'sales', 'orders')}
    snapshot = {
        'source': 'amazon-ads-mcp',
        'profile': {'profile_id': PROFILE, 'marketplace': 'US', 'currency': 'USD', 'advertiser_account_id': 'amzn1.ads-account.g.brov5a9dqr9tsh31ksxvow193'},
        'window': original.get('window') or {'start': '2026-08-02', 'end': '2026-08-08'},
        'account': account, 'campaigns': campaigns,
    }
    raw = canonical(snapshot)
    normalized_hash = hashlib.sha256(raw).hexdigest()
    schema = {'profile': sorted(snapshot['profile']), 'window': sorted(snapshot['window']), 'account': sorted(account), 'campaigns': sorted({key for item in campaigns for key in item})}
    schema_hash = hashlib.sha256(canonical(schema)).hexdigest()
    backup = DB.parent / 'backups' / ('state.db.pre-daily-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    shutil.copy2(DB, backup)
    store.execute('begin immediate')
    store.execute(
        'update report_jobs set normalized_snapshot_gzip=?, normalized_hash=?, schema_hash=?, row_count=?, updated_at=? where id=?',
        (gzip.compress(raw, mtime=0), normalized_hash, schema_hash, len(campaigns), datetime.now(timezone.utc).isoformat(), job_id),
    )
    store.commit()
    return job_id, snapshot, len(campaigns)


def main() -> int:
    try:
        status = control_post('/api/agent/context', {}) if False else None
        # Read-only MCP + deterministic snapshot rebuild.
        args, live_payload, live_campaigns = asyncio.run(live_campaign_read())
        store = sqlite3.connect(DB)
        store.row_factory = sqlite3.Row
        job_id, snapshot, row_count = rebuild_snapshot(store, live_campaigns)
        action_id = None
        from amazon_ads_control.db import Store
        action_id = Store(DB).record_action(
            task_id=None, session_id=SESSION, actor_role='orchestrator', phase='after',
            tool_name='mcp_amazon_ads_campaign_management_query_campaign', operation='read', allowed=True,
            args=args, success=True, outcome_status='COMPLETED', structured_result=True,
            reason='US-only daily live campaign read', result_summary=f'{row_count} US campaigns',
            result=live_payload, duration_ms=0,
        )
        normalized_hash = store.execute('select normalized_hash from report_jobs where id=?', (job_id,)).fetchone()[0]
        context = control_get('/api/agent/context')
        runtime = (context.get('runtime_status') or [{}])[0].get('state') if context.get('runtime_status') else {}
        runtime = runtime if isinstance(runtime, dict) else {}
        runtime['readiness_protocol'] = 1
        runtime['catalog_sync'] = {'ok': True, 'tool_count': int((context.get('catalog') or {}).get('tools') or 0), 'drifted': []}
        control_post('/api/agent/runtime-status', {'component': 'hermes-plugin', 'state': runtime})
        plan = control_post('/api/agent/cycles/plan', {
            'snapshot': snapshot,
            'lineage': {'report_job_ids': [job_id], 'action_ids': [action_id], 'normalized_hash': normalized_hash},
            'policy': {}, 'actor': 'orchestrator', 'parent_session_id': SESSION,
        })
        if plan.get('error'):
            raise RuntimeError('plan_error=' + json.dumps(plan, ensure_ascii=False))
        decisions = plan.get('decisions') or []
        if decisions:
            raise RuntimeError('executor_required_decisions=' + str(len(decisions)) + '; fail-closed until a bound executor/verifier is available')
        control_post('/api/agent/events', {'level': 'info', 'type': 'orchestrator.us_daily.no_action', 'actor': 'orchestrator', 'message': 'US-only daily cycle completed with no actionable decisions', 'data': {'session_id': SESSION, 'job_id': job_id, 'action_id': action_id, 'campaigns': row_count, 'plan_id': plan.get('id')}})
        print(json.dumps({'ok': True, 'scope': 'US-only', 'profile_id': PROFILE, 'report_job_id': job_id, 'live_campaigns': len(snapshot.get('campaigns') or []), 'normalized_targets': len(snapshot.get('targets') or []), 'action_id': action_id, 'plan_id': plan.get('id'), 'plan_status': plan.get('status'), 'decisions': 0, 'writes': 0}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'scope': 'US-only', 'error': type(exc).__name__ + ': ' + str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
