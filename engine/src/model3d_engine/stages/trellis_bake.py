"""TRELLIS.2's `o_voxel.postprocess.to_glb` with the texture baked tile by tile.

Runs in TRELLIS.2's interpreter, next to trellis_worker.py (it must not import engine code;
bake_tiles.py is a plain file beside it). The cleaning, remeshing and UV unwrapping are the
original's; only the bake differs: the original rasterizes the whole texture at once and keeps
several full-size float buffers on the card (an 8192 px bake asked for one more 1 GiB block with
11.1 of 12 GB in use and failed). Here each tile of at most bake_tiles.TILE texels is rasterized,
mapped back to the high-resolution mesh and sampled on its own, and its texels go to RAM, so the
card never holds more than one 4096 px tile's buffers.
"""

import cumesh
import cv2
import numpy as np
import nvdiffrast.torch as dr
import torch
import trimesh
import trimesh.visual
from flex_gemm.ops.grid_sample import grid_sample_3d
from PIL import Image

from bake_tiles import TILE, place, tile_clip, tiles

FACE_CHUNK = 100_000  # faces rasterized at once, as in the original


def to_glb(vertices, faces, attr_volume, coords, attr_layout, aabb, voxel_size=None, grid_size=None,
           decimation_target=1_000_000, texture_size=2048, remesh=False, remesh_band=1, remesh_project=0.9,
           mesh_cluster_threshold_cone_half_angle_rad=np.radians(90.0), mesh_cluster_refine_iterations=0,
           mesh_cluster_global_iterations=1, mesh_cluster_smooth_strength=1, tile=TILE):
    """The original's arguments and result; `tile` caps the side of each baked tile."""
    if isinstance(aabb, (list, tuple)):
        aabb = np.array(aabb)
    if isinstance(aabb, np.ndarray):
        aabb = torch.tensor(aabb, dtype=torch.float32, device=coords.device)
    if voxel_size is not None:
        if isinstance(voxel_size, float):
            voxel_size = [voxel_size] * 3
        if isinstance(voxel_size, (list, tuple)):
            voxel_size = np.array(voxel_size)
        if isinstance(voxel_size, np.ndarray):
            voxel_size = torch.tensor(voxel_size, dtype=torch.float32, device=coords.device)
        grid_size = ((aabb[1] - aabb[0]) / voxel_size).round().int()
    else:
        if isinstance(grid_size, int):
            grid_size = [grid_size] * 3
        if isinstance(grid_size, (list, tuple)):
            grid_size = np.array(grid_size)
        if isinstance(grid_size, np.ndarray):
            grid_size = torch.tensor(grid_size, dtype=torch.int32, device=coords.device)
        voxel_size = (aabb[1] - aabb[0]) / grid_size

    vertices, faces = vertices.cuda(), faces.cuda()
    mesh = cumesh.CuMesh()
    mesh.init(vertices, faces)
    mesh.fill_holes(max_hole_perimeter=3e-2)
    vertices, faces = mesh.read()
    bvh = cumesh.cuBVH(vertices, faces)
    if not remesh:
        mesh.simplify(decimation_target * 3)
        mesh.remove_duplicate_faces()
        mesh.repair_non_manifold_edges()
        mesh.remove_small_connected_components(1e-5)
        mesh.fill_holes(max_hole_perimeter=3e-2)
        mesh.simplify(decimation_target)
        mesh.remove_duplicate_faces()
        mesh.repair_non_manifold_edges()
        mesh.remove_small_connected_components(1e-5)
        mesh.fill_holes(max_hole_perimeter=3e-2)
        mesh.unify_face_orientations()
    else:
        center = aabb.mean(dim=0)
        scale = (aabb[1] - aabb[0]).max().item()
        resolution = grid_size.max().item()
        mesh.init(*cumesh.remeshing.remesh_narrow_band_dc(
            vertices, faces, center=center, scale=(resolution + 3 * remesh_band) / resolution * scale,
            resolution=resolution, band=remesh_band, project_back=remesh_project, bvh=bvh,
        ))
        mesh.simplify(decimation_target)

    out_vertices, out_faces, out_uvs, out_vmaps = mesh.uv_unwrap(
        compute_charts_kwargs={
            "threshold_cone_half_angle_rad": mesh_cluster_threshold_cone_half_angle_rad,
            "refine_iterations": mesh_cluster_refine_iterations,
            "global_iterations": mesh_cluster_global_iterations,
            "smooth_strength": mesh_cluster_smooth_strength,
        },
        return_vmaps=True,
    )
    out_vertices, out_faces, out_uvs, out_vmaps = out_vertices.cuda(), out_faces.cuda(), out_uvs.cuda(), out_vmaps.cuda()
    mesh.compute_vertex_normals()
    out_normals = mesh.read_vertex_normals()[out_vmaps]

    channels = attr_volume.shape[1]
    attrs = np.zeros((texture_size, texture_size, channels), np.float32)
    mask = np.zeros((texture_size, texture_size), bool)
    context = dr.RasterizeCudaContext()
    uv_cpu = out_uvs.cpu().numpy()
    volume_shape = torch.Size([1, channels, *grid_size.tolist()])
    volume_coords = torch.cat([torch.zeros_like(coords[:, :1]), coords], dim=-1)
    for row, column, side in tiles(texture_size, tile):
        clip = torch.from_numpy(tile_clip(uv_cpu, row, column, side, texture_size)).float().cuda()
        positions = torch.cat([clip, torch.zeros_like(clip[:, :1]), torch.ones_like(clip[:, :1])], dim=-1).unsqueeze(0)
        rast = torch.zeros((1, side, side, 4), device="cuda", dtype=torch.float32)
        for start in range(0, out_faces.shape[0], FACE_CHUNK):
            chunk, _ = dr.rasterize(context, positions, out_faces[start:start + FACE_CHUNK], resolution=[side, side])
            hit = chunk[..., 3:4] > 0
            chunk[..., 3:4] += start
            rast = torch.where(hit, chunk, rast)
            del chunk, hit
        tile_mask = rast[0, ..., 3] > 0
        tile_attrs = torch.zeros(side, side, channels, device="cuda")
        if tile_mask.any():
            position = dr.interpolate(out_vertices.unsqueeze(0), rast, out_faces)[0][0][tile_mask]
            _, face_id, uvw = bvh.unsigned_distance(position, return_uvw=True)
            position = (vertices[faces[face_id.long()]] * uvw.unsqueeze(-1)).sum(dim=1)
            tile_attrs[tile_mask] = grid_sample_3d(
                attr_volume, volume_coords, shape=volume_shape,
                grid=((position - aabb[0]) / voxel_size).reshape(1, -1, 3), mode="trilinear",
            )
            del position, face_id, uvw
        place(attrs, tile_attrs.cpu().numpy(), row, column)
        place(mask, tile_mask.cpu().numpy(), row, column)
        del clip, positions, rast, tile_mask, tile_attrs
        torch.cuda.empty_cache()

    to_byte = lambda name: np.clip(attrs[..., attr_layout[name]] * 255, 0, 255).astype(np.uint8)
    base_color, metallic, roughness, alpha = (to_byte(n) for n in ("base_color", "metallic", "roughness", "alpha"))
    del attrs
    holes = (~mask).astype(np.uint8)
    base_color = cv2.inpaint(base_color, holes, 3, cv2.INPAINT_TELEA)
    metallic = cv2.inpaint(metallic, holes, 1, cv2.INPAINT_TELEA)[..., None]
    roughness = cv2.inpaint(roughness, holes, 1, cv2.INPAINT_TELEA)[..., None]
    alpha = cv2.inpaint(alpha, holes, 1, cv2.INPAINT_TELEA)[..., None]
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=Image.fromarray(np.concatenate([base_color, alpha], axis=-1)),
        baseColorFactor=np.array([255, 255, 255, 255], dtype=np.uint8),
        metallicRoughnessTexture=Image.fromarray(np.concatenate([np.zeros_like(metallic), roughness, metallic], axis=-1)),
        metallicFactor=1.0, roughnessFactor=1.0, alphaMode="OPAQUE", doubleSided=not remesh,
    )
    vertices_np = out_vertices.cpu().numpy()
    faces_np = out_faces.cpu().numpy()
    uvs_np = uv_cpu.copy()
    normals_np = out_normals.cpu().numpy()
    vertices_np[:, 1], vertices_np[:, 2] = vertices_np[:, 2], -vertices_np[:, 1].copy()
    normals_np[:, 1], normals_np[:, 2] = normals_np[:, 2], -normals_np[:, 1].copy()
    uvs_np[:, 1] = 1 - uvs_np[:, 1]
    return trimesh.Trimesh(vertices=vertices_np, faces=faces_np, vertex_normals=normals_np, process=False,
                           visual=trimesh.visual.TextureVisuals(uv=uvs_np, material=material))
