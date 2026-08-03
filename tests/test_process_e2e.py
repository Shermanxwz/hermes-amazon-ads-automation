from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen

from amazon_ads_control.client import ControlClient
from amazon_ads_control.security import hash_password


class ProcessE2ETests(unittest.TestCase):
    def test_real_server_main_worker_audit_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            agent_key = "a" * 48
            env = os.environ.copy()
            env.update({
                "ADS_CONTROL_HOST": "127.0.0.1",
                "ADS_CONTROL_PORT": str(port),
                "ADS_CONTROL_DB": str(Path(tmp) / "state.db"),
                "ADS_CONTROL_PASSWORD_HASH": hash_password("this-is-a-long-test-password"),
                "ADS_CONTROL_AGENT_TOKEN": agent_key,
            })
            process = subprocess.Popen(
                [sys.executable, "-m", "amazon_ads_control.server"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    try:
                        with urlopen(base + "/health/ready", timeout=1) as response:
                            if response.status == 200:
                                break
                    except URLError:
                        pass
                    time.sleep(0.05)
                else:
                    self.fail("control plane did not become ready")

                control = ControlClient(base, agent_key)
                task = control.request("POST", "/api/agent/tasks", {
                    "title": "process e2e",
                    "kind": "optimization",
                    "objective": "lower one bid safely",
                    "write_allowed": True,
                    "parent_session_id": "main-e2e",
                    "expected_actions": [{
                        "idempotency_key": "process-e2e-bid",
                        "tool_contains": "update_bid",
                        "entity_id": "kw-e2e",
                        "field": "bid",
                        "before": 1.0,
                        "after": 0.9,
                        "reason": "test",
                    }],
                })
                task_id = task["id"]
                write = {
                    "tool_name": "amazon_ads_update_bid",
                    "args": {"keyword_id": "kw-e2e", "bid": 0.9},
                    "session_id": "main-e2e",
                }
                denied = control.request("POST", "/api/agent/tool-check", write)
                self.assertFalse(denied["allowed"])
                self.assertEqual(denied["actor_role"], "main")

                bound = control.request("POST", "/api/agent/worker-bind", {
                    "task_id": task_id,
                    "parent_session_id": "main-e2e",
                    "worker_session_id": "worker-e2e",
                    "worker_subagent_id": "sub-e2e",
                    "role": "worker",
                    "goal": f"execute [ads-task:{task_id}]",
                })
                self.assertEqual(bound["status"], "running")
                write["session_id"] = "worker-e2e"
                allowed = control.request("POST", "/api/agent/tool-check", write)
                self.assertTrue(allowed["allowed"])
                result = control.request("POST", "/api/agent/tool-result", {
                    **write, "result": {"ok": True}, "duration_ms": 12,
                })
                self.assertTrue(result["success"])
                duplicate = control.request("POST", "/api/agent/tool-check", write)
                self.assertFalse(duplicate["allowed"])
                self.assertIn("already completed", duplicate["reason"])
                completed = control.request("POST", "/api/agent/worker-stop", {
                    "worker_session_id": "worker-e2e",
                    "status": "completed",
                    "summary": "read-back verified",
                    "verification": {"read_back_bid": 0.9},
                })
                self.assertTrue(completed["ok"])
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                output = process.stdout.read() if process.stdout else ""
                if process.stdout:
                    process.stdout.close()
                self.assertIn(process.returncode, (0, -15), output)


if __name__ == "__main__":
    unittest.main()
