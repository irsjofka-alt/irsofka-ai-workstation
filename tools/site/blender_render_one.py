#!/usr/bin/env python3
"""blender_render_one.py — render satu .blend jadi PNG studio 1280x1280.

Dijalankan DI DALAM Blender, bukan Python biasa:

    blender -b <file.blend> --python tools/site/blender_render_one.py -- \
        --out <png> --meta <json> [--res 1280] [--samples 32] [--engine BLENDER_EEVEE]

Skrip membaca seluruh MESH di scene, menghitung bounding box dunia, lalu memasang
kamera 3/4, tiga lampu area (key/fill/rim), dan ground netral. File .blend tidak
pernah disimpan — semua perubahan hanya di memori sesi render.

Baris terakhir stdout adalah `RENDER_RESULT {json}` untuk dibaca orchestrator
blender_render.py. Exit code 0 hanya bila PNG benar-benar tertulis.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
import time

import bpy
from mathutils import Vector

MARKER = "RENDER_RESULT "


def parse_args():
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    import argparse

    parser = argparse.ArgumentParser(description="Render satu .blend ala studio.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--meta", required=True)
    parser.add_argument("--res", type=int, default=1280)
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--engine", default="BLENDER_EEVEE")
    return parser.parse_args(argv)


def world_bbox(meshes):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf, math.inf, math.inf))
    hi = Vector((-math.inf, -math.inf, -math.inf))
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        for corner in evaluated.bound_box:
            point = evaluated.matrix_world @ Vector(corner)
            lo.x = min(lo.x, point.x); lo.y = min(lo.y, point.y); lo.z = min(lo.z, point.z)
            hi.x = max(hi.x, point.x); hi.y = max(hi.y, point.y); hi.z = max(hi.z, point.z)
    return lo, hi


def aim(obj, target):
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def add_area_light(name, location, target, size, power, color=(1.0, 1.0, 1.0)):
    data = bpy.data.lights.new(name, "AREA")
    data.shape = "DISK"
    data.size = size
    data.energy = power
    data.color = color
    light = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(light)
    light.location = location
    aim(light, target)
    return light


def neutral_material(name, color, roughness):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    bsdf = material.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*color, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
    return material


def setup_world(scene):
    world = scene.world or bpy.data.worlds.new("Station Studio")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    background = nodes.get("Background") or nodes.new("ShaderNodeBackground")
    background.inputs["Color"].default_value = (0.045, 0.05, 0.058, 1.0)
    background.inputs["Strength"].default_value = 0.55
    output = nodes.get("World Output") or nodes.new("ShaderNodeOutputWorld")
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])


def render(args):
    started = time.time()
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type in {"CAMERA", "LIGHT"}:
            bpy.data.objects.remove(obj, do_unlink=True)

    meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("scene tidak punya MESH")
    for obj in meshes:
        obj.hide_render = False

    bpy.context.view_layer.update()
    lo, hi = world_bbox(meshes)
    center = (lo + hi) / 2
    dims = hi - lo
    diag = dims.length
    if not math.isfinite(diag) or diag <= 0:
        raise RuntimeError("bounding box tidak valid")

    # Ground netral tepat di bawah objek.
    bpy.ops.mesh.primitive_plane_add(
        size=diag * 8, location=(center.x, center.y, lo.z - diag * 0.002))
    ground = bpy.context.active_object
    ground.name = "Studio_Ground"
    ground.data.materials.append(neutral_material("Studio_Ground_Mat", (0.17, 0.17, 0.18), 0.92))

    setup_world(scene)

    # Kamera 3/4: dari kanan-depan, sedikit di atas pusat objek.
    cam_data = bpy.data.cameras.new("Studio_Cam")
    cam_data.lens = 50.0
    cam_data.sensor_width = 36.0
    cam_data.sensor_fit = "AUTO"
    cam = bpy.data.objects.new("Studio_Cam", cam_data)
    scene.collection.objects.link(cam)
    fov = 2 * math.atan((cam_data.sensor_width / 2) / cam_data.lens)
    radius = diag / 2
    distance = (radius / math.sin(fov / 2)) * 1.18
    cam_dir = Vector((1.0, -1.0, 0.62)).normalized()
    cam.location = center + cam_dir * distance
    cam_data.clip_start = max(diag * 0.001, 0.01)
    cam_data.clip_end = distance * 20 + diag * 10
    aim(cam, center)
    scene.camera = cam

    # Tiga lampu area; daya mengikuti kuadrat jarak supaya skala objek apa pun sama.
    key_dir = Vector((-0.55, -0.95, 1.05)).normalized()
    fill_dir = Vector((1.0, -0.75, 0.35)).normalized()
    rim_dir = Vector((-0.35, 1.0, 0.85)).normalized()
    for name, direction, dist_factor, size_factor, irradiance, color in (
        ("Studio_Key", key_dir, 1.7, 0.85, 7.0, (1.0, 0.96, 0.9)),
        ("Studio_Fill", fill_dir, 1.5, 1.1, 1.6, (0.85, 0.9, 1.0)),
        ("Studio_Rim", rim_dir, 1.6, 0.7, 3.2, (0.9, 0.95, 1.0)),
    ):
        distance_light = diag * dist_factor
        add_area_light(name, center + direction * distance_light, center,
                       diag * size_factor, irradiance * 4 * math.pi * distance_light ** 2,
                       color)

    scene.render.engine = args.engine
    if hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = args.samples
    for attr, value in (("use_raytracing", True), ("use_shadows", True)):
        try:
            setattr(scene.eevee, attr, value)
        except (AttributeError, TypeError):
            pass
    scene.render.resolution_x = args.res
    scene.render.resolution_y = args.res
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = args.out

    bpy.ops.render.render(write_still=True)
    if not bpy.data.images.get("Render Result"):
        raise RuntimeError("Render Result tidak ada")

    elapsed = round(time.time() - started, 2)
    meta = {
        "ok": True,
        "file": bpy.data.filepath,
        "scene": scene.name,
        "objects": [obj.name for obj in meshes],
        "object_count": len(meshes),
        "bbox_min": [round(v, 4) for v in lo],
        "bbox_max": [round(v, 4) for v in hi],
        "bbox_size": [round(v, 4) for v in dims],
        "bbox_diagonal": round(diag, 4),
        "resolution": [args.res, args.res],
        "engine": scene.render.engine,
        "samples": args.samples,
        "camera_3q": [round(v, 4) for v in cam.location],
        "render_seconds": elapsed,
        "rendered_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "blender_version": bpy.app.version_string,
        "png": args.out,
    }
    return meta


def main() -> int:
    args = parse_args()
    try:
        meta = render(args)
    except Exception as exc:  # noqa: BLE001 — kegagalan apa pun harus jadi JSON
        print(MARKER + json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    with open(args.meta, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(MARKER + json.dumps({"ok": True, **{key: meta[key] for key in (
        "object_count", "resolution", "engine", "samples", "render_seconds", "png")}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
