import asyncio
import unittest
from unittest import mock

from daemon.services import docker_service


class _Proc:
    def __init__(self, out=b""):
        self.out = out

    async def communicate(self):
        return self.out, b""

    async def wait(self):
        return 0


class PruneOrphanedImagesTests(unittest.TestCase):
    def _removed(self, pending):
        images = {"a": "hub/a:1", "b": "hub/b:1", "c": "hub/c:1"}
        removed = []

        async def exec_(*cmd, **_):
            if cmd[:2] == ("docker", "images"):
                return _Proc("\n".join(images.values()).encode())
            if cmd[:2] == ("docker", "rmi"):
                removed.append(cmd[-1])
            return _Proc()

        with mock.patch.object(docker_service, "get_installed_slugs", mock.AsyncMock(return_value={"a"})), \
                mock.patch.object(docker_service, "_parse_compose_images", lambda s: [images[s]]), \
                mock.patch.object(docker_service, "_pending_actions", pending), \
                mock.patch("asyncio.create_subprocess_exec", exec_):
            asyncio.run(docker_service._prune_orphaned_images())
        return removed

    def test_an_image_no_recipe_needs_is_removed(self):
        self.assertEqual(self._removed({}), ["hub/b:1", "hub/c:1"])

    def test_a_recipe_still_installing_keeps_its_new_image(self):
        self.assertEqual(self._removed({"b": "installing", "c": "stopping"}), ["hub/c:1"])


if __name__ == "__main__":
    unittest.main()
