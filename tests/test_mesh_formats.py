import gzip
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


    def test_spz_holds_the_splat_in_spz_axes(self):
        """One Gaussian, read back by the layout of Niantic's spz (load-spz.cc)."""
        names = ["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
                 "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
        row = np.array([[0.25, -0.125, 0.5, 0, 0, 0, 1.0, 0.0, -1.0, 0.0,
                         -4.0, -5.0, -6.0, 0.5, 0.5, 0.5, 0.5]], dtype="<f4")
        ply = ("ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
               + "".join(f"property float {n}\n" for n in names) + "end_header\n").encode() + row.tobytes()
        raw = gzip.decompress(mesh_service._spz(ply))
        magic, version, count = np.frombuffer(raw[:12], dtype="<u4")
        self.assertEqual((magic, version, count, raw[12], raw[13]), (0x5053474E, 2, 1, 0, 16))
        data = raw[16:]
        xyz = [int.from_bytes(data[i:i + 3], "little", signed=True) / 2**16 for i in (0, 3, 6)]
        self.assertEqual(xyz, [0.25, 0.125, -0.5])                  # Y and Z turned over
        self.assertEqual(data[9], 128)                              # sigmoid(0) = half opaque
        self.assertEqual(list(data[10:13]), [166, 128, 89])         # 0.5 + 0.15 x f_dc
        self.assertEqual(list(data[13:16]), [96, 80, 64])           # (log-scale + 10) x 16
        self.assertEqual(list(data[16:19]), [191, 64, 64])          # (x, -y, -z) of (w, x, y, z)
        self.assertEqual(len(data), 19)

    def test_a_splats_spz_belongs_to_its_ply(self):
        splat_id = "fedcba9876543210fedcba9876543210"
        self.assertEqual(media_store.master(splat_id + ".spz"), splat_id + ".ply")
        self.assertEqual(media_store.name_in_url(f"https://hub/splats/{splat_id}.spz"), splat_id + ".spz")


if __name__ == "__main__":
    unittest.main()
