"""start.py has to run as a script and as notebook-cell code, where __file__ is missing."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from tests.helpers import free_port

ROOT = Path(__file__).resolve().parent.parent

CELL = """
import asyncio, os, sys
source = open(os.environ["START_PY"]).read()
namespace = {"__name__": "__main__"}

async def cell():
    exec(compile(source, "<cell>", "exec"), namespace)

assert "__file__" not in namespace
asyncio.run(cell())
import time
time.sleep(60)
"""


class StartTests(unittest.TestCase):
    def env(self, port):
        env = {key: value for key, value in os.environ.items() if not key.startswith(("RAGFLOW_", "CDSW_"))}
        env.update(CDSW_APP_PORT=str(port), RAGFLOW_DATA_DIR=tempfile.mkdtemp(prefix="ragflow-start-"), START_PY=str(ROOT / "start.py"))
        return env

    def wait_healthy(self, port, process):
        for _ in range(200):
            if process.poll() is not None:
                self.fail(f"The process exited early: {process.stderr.read().decode()}")
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        self.fail("The app never became healthy")

    def stop(self, process):
        process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()

    def run_and_check(self, args, cwd):
        port = free_port()
        process = subprocess.Popen(args, cwd=cwd, env=self.env(port), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_healthy(port, process)
            self.assertEqual(httpx.get(f"http://127.0.0.1:{port}/mcp/", follow_redirects=True).status_code < 500, True)
        finally:
            self.stop(process)

    def test_runs_as_a_script(self):
        self.run_and_check([sys.executable, str(ROOT / "start.py")], cwd=tempfile.gettempdir())

    def test_runs_as_notebook_cell_code_from_the_project_folder(self):
        self.run_and_check([sys.executable, "-c", CELL], cwd=ROOT)

    def test_runs_as_notebook_cell_code_from_a_subfolder(self):
        self.run_and_check([sys.executable, "-c", CELL], cwd=ROOT / "frontend")

    def test_a_cell_outside_the_project_explains_what_to_do(self):
        result = subprocess.run(
            [sys.executable, "-c", CELL],
            cwd=tempfile.gettempdir(),
            env=self.env(free_port()),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RAGFLOW_ROOT", result.stderr)

    def test_ragflow_root_points_a_cell_at_the_project(self):
        port = free_port()
        env = self.env(port)
        env["RAGFLOW_ROOT"] = str(ROOT)
        process = subprocess.Popen([sys.executable, "-c", CELL], cwd=tempfile.gettempdir(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_healthy(port, process)
        finally:
            self.stop(process)


if __name__ == "__main__":
    unittest.main()
