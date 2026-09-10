"""Test local entry points without Docker, database credentials or application imports."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestEntrypoints(unittest.TestCase):
    def run_entrypoint(self, entry: str, exit_code: int) -> None:
        with tempfile.TemporaryDirectory(prefix="test-entrypoint-") as directory:
            root = Path(directory) / "checkout with spaces"
            scripts = root / "scripts"
            backend = root / "backend"
            scripts.mkdir(parents=True)
            (backend / "scripts").mkdir(parents=True)
            for name in ("test.sh", "test-local.sh"):
                shutil.copyfile(ROOT / "scripts" / name, scripts / name)
            delegate = backend / "scripts" / "tests-start.sh"
            delegate.write_text(
                '#!/usr/bin/env bash\nset -eu\nprintf "%s\\n" "$PWD" "$@"\n'
                f"exit {exit_code}\n",
                encoding="utf-8",
            )
            fake_bin = root / "bin"
            fake_bin.mkdir()
            marker = root / "unsafe-command-called"
            for command in ("docker", "docker-compose", "sudo"):
                stub = fake_bin / command
                stub.write_text(
                    '#!/usr/bin/env bash\nprintf unsafe > "$UNSAFE_MARKER"\nexit 97\n',
                    encoding="utf-8",
                )
                stub.chmod(0o755)
            env = os.environ | {
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
                "UNSAFE_MARKER": str(marker),
            }
            result = subprocess.run(
                ["bash", str(scripts / entry), "Coverage with spaces", "literal *"],
                cwd=directory,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, exit_code, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [str(backend), "Coverage with spaces", "literal *"],
            )
            self.assertFalse(marker.exists(), "Test wrapper invoked Docker or sudo")

    def test_local_preserves_arguments_and_working_directory(self) -> None:
        self.run_entrypoint("test-local.sh", 0)

    def test_local_propagates_failure(self) -> None:
        self.run_entrypoint("test-local.sh", 42)

    def test_alias_preserves_arguments_and_working_directory(self) -> None:
        self.run_entrypoint("test.sh", 0)

    def test_alias_propagates_failure(self) -> None:
        self.run_entrypoint("test.sh", 42)


if __name__ == "__main__":
    unittest.main()
