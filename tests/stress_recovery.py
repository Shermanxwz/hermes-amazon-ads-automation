#!/usr/bin/env python3
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
import json
import tempfile
import threading

from amazon_ads_control.api import build_server
from amazon_ads_control.config import Settings
from amazon_ads_control.security import hash_password
from helpers import Environment


def main() -> int:
    env=Environment()
    try:
        _,task,decision=env.one_decision_task()
        def reserve(i):
            try:
                env.store.reserve_decision(decision["id"],task["id"],f"e{i}",300)
                return 1
            except (ValueError,KeyError):
                return 0
        with ThreadPoolExecutor(max_workers=32) as pool:
            assert sum(pool.map(reserve,range(100))) == 1
        event={"profile_id":"p1","dataset_id":"budget","dedupe_key":"same","payload":{"budgetUsagePercent":96}}
        result=env.service.ingest_stream({"events":[event for _ in range(1000)]})
        assert result == {"inserted":1,"duplicates":999}, result
        assert env.store.integrity_check(quick=False)["ok"]
        def write_event(i):
            env.store.event("info","stress.write","stress",None,f"event {i}",{"i":i}); return i
        def backup(i):
            path=Path(env.temp.name)/f"backup-{i}.db"
            result=env.store.backup_to(path)
            return result["integrity"]["ok"] and path.exists()
        with ThreadPoolExecutor(max_workers=24) as pool:
            write_futures=[pool.submit(write_event,i) for i in range(500)]
            backup_futures=[pool.submit(backup,i) for i in range(10)]
            assert all(f.result() == i for i,f in enumerate(write_futures))
            assert all(f.result() for f in backup_futures)
        assert env.store.integrity_check(quick=False)["ok"]
    finally:
        env.close()

    with tempfile.TemporaryDirectory() as td:
        settings=Settings(host="127.0.0.1",port=0,db_path=Path(td)/"state.db",public_origin="",control_password_hash=hash_password("correct horse battery staple"),agent_token="a"*48,session_ttl_seconds=3600,max_sessions=32,retention_days=30,allow_remote_bind=False)
        server=build_server(settings); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start(); port=server.server_address[1]
        try:
            def health(_):
                connection=HTTPConnection("127.0.0.1",port,timeout=5); connection.request("GET","/health/ready"); response=connection.getresponse(); body=json.loads(response.read()); connection.close(); return response.status==200 and body["ok"]
            with ThreadPoolExecutor(max_workers=32) as pool:
                assert all(pool.map(health,range(200)))
            assert server.RequestHandlerClass.app.store.integrity_check(quick=False)["ok"]
        finally:
            server.shutdown(); server.server_close(); thread.join()
    print("stress-recovery: OK")
    return 0

if __name__ == "__main__": raise SystemExit(main())
