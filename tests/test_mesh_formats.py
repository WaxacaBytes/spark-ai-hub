import io
import unittest
import zipfile

import numpy as np
import trimesh
from PIL import Image

from daemon.services import media_store, mesh_service


def _textured_glb() -> bytes:
    """A unit box, taller than wide (Y-up like every engine's GLB), with a
    red/blue texture."""
    box = trimesh.creation.box(extents=(1.0, 2.0, 0.5))
    uv = (box.vertices[:, :2] - box.vertices[:, :2].min(0)) / np.ptp(box.vertices[:, :2], 0)
    texture = Image.new("RGB", (8, 8), (255, 0, 0))
    texture.paste((0, 0, 255), (0, 0, 8, 4))
    box.visual = trimesh.visual.TextureVisuals(uv=uv, image=texture)
    return trimesh.Scene(box).export(file_type="glb")


class MeshFormatsTest(unittest.TestCase):
    def test_stl_is_shape_only_and_z_up(self):
        mesh = trimesh.load(io.BytesIO(mesh_service._stl(_textured_glb())), file_type="stl")
        extent = mesh.bounds[1] - mesh.bounds[0]
        self.assertEqual(len(mesh.faces), 12)
        self.assertAlmostEqual(extent[2], 2.0, places=4)    # the GLB's Y (height) is now Z

    def test_ply_carries_the_texture_as_vertex_colours(self):
        mesh = trimesh.load(io.BytesIO(mesh_service._ply(_textured_glb())), file_type="ply")
        colours = {tuple(c) for c in mesh.visual.vertex_colors[:, :3]}
        self.assertTrue(any(c[0] > 200 and c[2] < 50 for c in colours))   # red
        self.assertTrue(any(c[2] > 200 and c[0] < 50 for c in colours))   # blue

    def test_obj_zip_holds_obj_mtl_and_texture(self):
        archive = zipfile.ZipFile(io.BytesIO(mesh_service._obj_zip(_textured_glb())))
        names = archive.namelist()
        self.assertIn("model.obj", names)
        self.assertIn("model.mtl", names)
        self.assertTrue(any(n.endswith(".png") for n in names))
        self.assertIn(b"mtllib model.mtl", archive.read("model.obj"))

    def test_other_formats_belong_to_the_glb(self):
        mesh_id = "0123456789abcdef0123456789abcdef"
        for ext in (".stl", ".ply", ".obj.zip"):
            self.assertEqual(media_store.master(mesh_id + ext), mesh_id + ".glb")
            self.assertEqual(media_store.name_in_url(f"https://hub/meshes/{mesh_id}{ext}"), mesh_id + ext)
        self.assertEqual(media_store.master(mesh_id + ".png"), mesh_id + ".png")
        self.assertIsNone(mesh_service.EXPORT_NAME_RE.match(mesh_id + ".glb"))


if __name__ == "__main__":
    unittest.main()
