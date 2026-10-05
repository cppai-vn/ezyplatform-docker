"""Optional Linux container tests: RUN_DOCKER_TESTS=1 python3 -m unittest discover -s tests -v."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("RUN_DOCKER_TESTS") == "1", "Docker integration tests opt-in")
class EntrypointTests(unittest.TestCase):
    def run_fixture(self, curl_script, **overrides):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / "settings").mkdir()
            (directory / "settings/setup.properties").write_text(
                "datasource.jdbc_url=old\ndatasource.username=old\n"
                "datasource.password=old\ntables.create_manually=false\n")
            (directory / "admin").mkdir()
            (directory / "admin/start-service.sh").write_text(
                '#!/bin/bash\njava -cp test AdminStartup > /dev/null 2>&1 &\n')
            (directory / "cli.sh").write_text(
                '#!/bin/bash\necho "fixture launch requested: $1"\n'
                'echo "fixture fatal startup exception" >> logs/admin-console.log\n')
            (directory / "bin").mkdir()
            curl = directory / "bin/curl"
            curl.write_text("#!/bin/bash\n" + curl_script)
            curl.chmod(0o755)
            environment = {"DB_USERNAME": "local-test", "DB_PASSWORD": "secret|with&symbols",
                           "DB_HOST": "mysql", "AUTO_START_WEB": "false",
                           "STARTUP_TIMEOUT_SECONDS": "1", "HEALTHCHECK_INTERVAL_SECONDS": "1",
                           "HEALTHCHECK_FAILURE_THRESHOLD": "2",
                           "PATH": "/app/ezyplatform/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
            environment.update(overrides)
            command = ["docker", "--context", os.environ.get("DOCKER_TEST_CONTEXT", "orbstack"),
                       "run", "--rm", "--network", "none", "-v", f"{directory}:/app/ezyplatform",
                       "-v", f"{ROOT / 'entrypoint.sh'}:/fixture-entrypoint.sh:ro",
                       "--entrypoint", "bash"]
            for name, value in environment.items():
                command.extend(["-e", f"{name}={value}"])
            command.extend([os.environ.get("DOCKER_TEST_IMAGE", "ezyplatform-local-fixed:1.5.0"),
                            "/fixture-entrypoint.sh"])
            result = subprocess.run(command, capture_output=True, text=True, timeout=20)
            return result, (directory / "settings/setup.properties").read_text(), (
                directory / "admin/start-service.sh").read_text()

    def test_startup_timeout_retains_exception_and_special_password(self):
        result, settings, launcher = self.run_fixture("echo 000; exit 7\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("HTTP startup timed out", result.stderr)
        self.assertIn("fixture fatal startup exception", result.stderr)
        self.assertIn("datasource.password=secret|with&symbols", settings)
        self.assertIn(">> logs/admin-console.log 2>&1 &", launcher)
        self.assertNotIn("secret|with&symbols", result.stdout + result.stderr)

    def test_healthy_then_repeated_http_failures_exit(self):
        result, _, _ = self.run_fixture(
            'counter=/app/ezyplatform/counter\n'
            'n=0; [[ ! -f "$counter" ]] || read -r n < "$counter"\n'
            'n=$((n+1)); echo "$n" > "$counter"\n'
            'if (( n == 1 )); then echo 302; else echo 500; fi\n')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("HTTP readiness confirmed", result.stdout)
        self.assertIn("HTTP health check failed (2/2)", result.stderr)
        self.assertIn("exiting for the orchestrator", result.stderr)

    def test_invalid_timeout_is_rejected_before_launch(self):
        result, _, _ = self.run_fixture("echo 200\n", STARTUP_TIMEOUT_SECONDS="0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("must be a positive integer", result.stderr)
        self.assertNotIn("fixture launch requested", result.stdout)


if __name__ == "__main__":
    unittest.main()
