import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from daemon.services import docker_service


class _Proc:
    returncode = 0

    def __init__(self):
        async def lines():
            return
            yield
        self.stdout = lines()

    async def wait(self):
        return 0


class UpdateRecipeTests(unittest.TestCase):
    def _commands(self, *, running, build=True, lora=False):
        """The docker compose commands an update runs."""
        ran = []

        async def exec_(*cmd, **_):
            ran.append(cmd)
            return _Proc()

        recipe = SimpleNamespace(docker=SimpleNamespace(build=build), lora=object() if lora else None)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docker-compose.yml").write_text("services: {}\n")
            with mock.patch.object(docker_service, "get_recipe_dir", lambda s: Path(tmp)), \
                    mock.patch.object(docker_service, "ensure_runtime_env", lambda d: (None, False)), \
                    mock.patch.object(docker_service, "get_recipe", lambda s: recipe), \
                    mock.patch.object(docker_service, "is_recipe_running", mock.AsyncMock(return_value=running)), \
                    mock.patch.object(docker_service, "_compose_cmd", lambda s, d: ["compose"]), \
                    mock.patch.object(docker_service, "_prune_build_cache", mock.AsyncMock(return_value="")), \
                    mock.patch.object(docker_service, "_prune_orphaned_images", mock.AsyncMock(return_value="")), \
                    mock.patch("asyncio.create_subprocess_exec", exec_):
                async def run():
                    return [line async for line in docker_service.update_recipe("x")]
                asyncio.run(run())
        return ran

    def test_a_stopped_app_is_rebuilt_but_not_started(self):
        self.assertEqual(self._commands(running=False), [("compose", "build")])

    def test_a_running_app_is_rebuilt_and_recreated(self):
        self.assertEqual(self._commands(running=True), [("compose", "up", "-d", "--build")])

    def test_a_lora_is_only_built(self):
        self.assertEqual(self._commands(running=True, lora=True), [("compose", "build")])

    def test_a_stopped_pulled_app_is_pulled_but_not_started(self):
        self.assertEqual(self._commands(running=False, build=False), [("compose", "pull")])

    def test_a_running_pulled_app_is_recreated(self):
        self.assertEqual(self._commands(running=True, build=False), [("compose", "pull"), ("compose", "up", "-d")])


if __name__ == "__main__":
    unittest.main()
