"""Boundary authoring and pure UV curve helpers for Quick SDF.

The geometry functions in this module deliberately have no Blender dependency so
they can be tested with the system Python.  The operator layer is defined only
when :mod:`bpy` is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, hypot
from typing import Any, Iterable, MutableSequence, Sequence


EPSILON = 1.0e-9
Vec2 = tuple[float, float]


@dataclass(frozen=True)
class BezierKnot:
    co: Vec2
    handle_left: Vec2
    handle_right: Vec2


def _xy(point: Any) -> Vec2:
    """Return a 2D tuple from a Blender property, vector, or tuple."""
    if hasattr(point, "co"):
        co = point.co
        return float(co[0]), float(co[1])
    if hasattr(point, "u") and hasattr(point, "v"):
        return float(point.u), float(point.v)
    return float(point[0]), float(point[1])


def _distance(a: Vec2, b: Vec2) -> float:
    return hypot(b[0] - a[0], b[1] - a[1])


def cubic_bezier(a: Vec2, b: Vec2, c: Vec2, d: Vec2, t: float) -> Vec2:
    omt = 1.0 - t
    omt2 = omt * omt
    t2 = t * t
    return (
        omt2 * omt * a[0] + 3.0 * omt2 * t * b[0] + 3.0 * omt * t2 * c[0] + t2 * t * d[0],
        omt2 * omt * a[1] + 3.0 * omt2 * t * b[1] + 3.0 * omt * t2 * c[1] + t2 * t * d[1],
    )


def sample_bezier(knots: Sequence[BezierKnot], samples_per_segment: int = 12, closed: bool = False) -> list[Vec2]:
    """Convert Bezier knots to a polyline suitable for rasterization."""
    if not knots:
        return []
    if len(knots) == 1:
        return [knots[0].co]
    samples_per_segment = max(2, int(samples_per_segment))
    segment_count = len(knots) if closed else len(knots) - 1
    result: list[Vec2] = []
    for index in range(segment_count):
        current = knots[index]
        following = knots[(index + 1) % len(knots)]
        for sample in range(samples_per_segment):
            if index and sample == 0:
                continue
            t = sample / float(samples_per_segment)
            result.append(cubic_bezier(current.co, current.handle_right, following.handle_left, following.co, t))
    if not closed:
        result.append(knots[-1].co)
    return result


def resample_polyline(points: Sequence[Any], count: int, closed: bool = False) -> list[Vec2]:
    """Arc-length resample a polyline.

    Closed output intentionally does not duplicate its first point.  Degenerate
    curves are returned as repeated points so interpolation remains predictable.
    """
    source = [_xy(point) for point in points]
    count = max(0, int(count))
    if count == 0 or not source:
        return []
    if len(source) == 1:
        return [source[0]] * count
    if closed and _distance(source[0], source[-1]) <= EPSILON:
        source.pop()
    segment_count = len(source) if closed else len(source) - 1
    lengths: list[float] = []
    total = 0.0
    for index in range(segment_count):
        length = _distance(source[index], source[(index + 1) % len(source)])
        lengths.append(length)
        total += length
    if total <= EPSILON:
        return [source[0]] * count

    denominator = count if closed else max(1, count - 1)
    targets = [total * (index / denominator) for index in range(count)]
    output: list[Vec2] = []
    segment = 0
    traversed = 0.0
    for target in targets:
        while segment < segment_count - 1 and traversed + lengths[segment] < target:
            traversed += lengths[segment]
            segment += 1
        start = source[segment]
        end = source[(segment + 1) % len(source)]
        length = lengths[segment]
        t = 0.0 if length <= EPSILON else (target - traversed) / length
        output.append((start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t))
    return output


def interpolate_curves(
    before: Sequence[Any],
    after: Sequence[Any],
    factor: float,
    *,
    count: int | None = None,
    closed: bool = False,
) -> list[Vec2]:
    """Arc-length align and linearly interpolate two UV curves."""
    if not before or not after:
        return [_xy(point) for point in (before or after)]
    count = count or max(len(before), len(after), 2)
    left = resample_polyline(before, count, closed)
    right = resample_polyline(after, count, closed)
    factor = max(0.0, min(1.0, float(factor)))
    return [
        (a[0] + (b[0] - a[0]) * factor, a[1] + (b[1] - a[1]) * factor)
        for a, b in zip(left, right)
    ]


def interpolate_angle_keys(keys: Sequence[Any], angle: float, count: int | None = None, closed: bool = False) -> list[Vec2]:
    """Evaluate a collection of ``angle``/``points`` boundary keys."""
    available = sorted((float(key.angle), key) for key in keys if len(getattr(key, "points", ())))
    if not available:
        return []
    if angle <= available[0][0]:
        return resample_polyline(available[0][1].points, count or len(available[0][1].points), closed)
    if angle >= available[-1][0]:
        return resample_polyline(available[-1][1].points, count or len(available[-1][1].points), closed)
    for (a0, key0), (a1, key1) in zip(available, available[1:]):
        if a0 <= angle <= a1:
            factor = 0.0 if abs(a1 - a0) <= EPSILON else (angle - a0) / (a1 - a0)
            return interpolate_curves(key0.points, key1.points, factor, count=count, closed=closed)
    return []


def _orientation(a: Vec2, b: Vec2, c: Vec2) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Vec2, b: Vec2, p: Vec2) -> bool:
    return (
        min(a[0], b[0]) - EPSILON <= p[0] <= max(a[0], b[0]) + EPSILON
        and min(a[1], b[1]) - EPSILON <= p[1] <= max(a[1], b[1]) + EPSILON
        and abs(_orientation(a, b, p)) <= EPSILON
    )


def segments_intersect(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> bool:
    ab_c, ab_d = _orientation(a, b, c), _orientation(a, b, d)
    cd_a, cd_b = _orientation(c, d, a), _orientation(c, d, b)
    if (ab_c > EPSILON and ab_d < -EPSILON or ab_c < -EPSILON and ab_d > EPSILON) and (
        cd_a > EPSILON and cd_b < -EPSILON or cd_a < -EPSILON and cd_b > EPSILON
    ):
        return True
    return any((_on_segment(a, b, c), _on_segment(a, b, d), _on_segment(c, d, a), _on_segment(c, d, b)))


def curve_self_intersects(points: Sequence[Any], closed: bool = False) -> bool:
    source = [_xy(point) for point in points]
    segment_count = len(source) if closed else len(source) - 1
    if segment_count < 2:
        return False
    for first in range(segment_count):
        a, b = source[first], source[(first + 1) % len(source)]
        for second in range(first + 1, segment_count):
            if second == first + 1 or (closed and first == 0 and second == segment_count - 1):
                continue
            c, d = source[second], source[(second + 1) % len(source)]
            if segments_intersect(a, b, c, d):
                return True
    return False


def validate_curve(points: Sequence[Any], closed: bool = False) -> tuple[bool, str]:
    source = [_xy(point) for point in points]
    minimum = 3 if closed else 2
    if len(source) < minimum:
        return False, f"Boundary needs at least {minimum} points"
    if any(not (-EPSILON <= u <= 1.0 + EPSILON and -EPSILON <= v <= 1.0 + EPSILON) for u, v in source):
        return False, "Boundary points must remain inside the 0-1 UV tile"
    if curve_self_intersects(source, closed=closed):
        return False, "Boundary self-intersects"
    return True, ""


def closest_boundary_index(point: Any, island_boundary: Sequence[Any]) -> tuple[int, float]:
    p = _xy(point)
    if not island_boundary:
        return -1, float("inf")
    distances = [_distance(p, _xy(candidate)) for candidate in island_boundary]
    index = min(range(len(distances)), key=distances.__getitem__)
    return index, distances[index]


def close_open_curve_along_island(
    curve: Sequence[Any],
    island_boundary: Sequence[Any],
    *,
    tolerance: float = 0.01,
    prefer_long_arc: bool = False,
) -> list[Vec2]:
    """Close an open curve using one of the two arcs on an island boundary.

    Raises ``ValueError`` when both curve ends are not attached to the supplied
    island perimeter.  The default shorter arc keeps authoring predictable.
    """
    source = [_xy(point) for point in curve]
    boundary = [_xy(point) for point in island_boundary]
    if len(source) < 2 or len(boundary) < 3:
        raise ValueError("An open curve and a closed island boundary are required")
    start, start_distance = closest_boundary_index(source[0], boundary)
    end, end_distance = closest_boundary_index(source[-1], boundary)
    if max(start_distance, end_distance) > tolerance:
        raise ValueError("Open boundary ends must touch the same UV island perimeter")
    forward: list[Vec2] = []
    index = end
    while True:
        forward.append(boundary[index])
        if index == start:
            break
        index = (index + 1) % len(boundary)
    backward: list[Vec2] = []
    index = end
    while True:
        backward.append(boundary[index])
        if index == start:
            break
        index = (index - 1) % len(boundary)
    length = lambda path: sum(_distance(a, b) for a, b in zip(path, path[1:]))
    short, long = (forward, backward) if length(forward) <= length(backward) else (backward, forward)
    return source + (long if prefer_long_arc else short)


def uv_boundary_loops(faces: Iterable[Sequence[Any]], precision: int = 7) -> list[list[Vec2]]:
    """Extract ordered UV perimeters from face-corner UV coordinates.

    UV edges shared by two faces cancel, including meshes whose geometric edge
    is split at a UV seam.  The helper accepts ordinary tuples as well as Blender
    vectors and is useful for tests without Blender.
    """
    edge_records: dict[tuple[tuple[float, float], tuple[float, float]], list[Any]] = {}

    def token(point: Vec2) -> tuple[float, float]:
        return round(point[0], precision), round(point[1], precision)

    for face in faces:
        points = [_xy(point) for point in face]
        if len(points) < 3:
            continue
        for a, b in zip(points, points[1:] + points[:1]):
            ta, tb = token(a), token(b)
            key = (ta, tb) if ta <= tb else (tb, ta)
            record = edge_records.setdefault(key, [0, a, b])
            record[0] += 1
    segments = [(record[1], record[2]) for record in edge_records.values() if record[0] == 1]
    adjacency: dict[tuple[float, float], list[int]] = {}
    for index, (a, b) in enumerate(segments):
        adjacency.setdefault(token(a), []).append(index)
        adjacency.setdefault(token(b), []).append(index)
    unused = set(range(len(segments)))
    loops: list[list[Vec2]] = []
    while unused:
        segment_index = unused.pop()
        a, b = segments[segment_index]
        path = [a, b]
        current_token = token(b)
        start_token = token(a)
        while current_token != start_token:
            candidates = [index for index in adjacency.get(current_token, ()) if index in unused]
            if not candidates:
                break
            next_index = candidates[0]
            unused.remove(next_index)
            first, second = segments[next_index]
            following = second if token(first) == current_token else first
            path.append(following)
            current_token = token(following)
        if len(path) >= 4 and token(path[0]) == token(path[-1]):
            path.pop()
        if len(path) >= 3:
            loops.append(path)
    loops.sort(key=lambda loop: abs(polygon_signed_area(loop)), reverse=True)
    return loops


def polygon_signed_area(points: Sequence[Any]) -> float:
    polygon = [_xy(point) for point in points]
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))


def point_in_polygon(point: Any, polygon: Sequence[Any]) -> bool:
    p = _xy(point)
    vertices = [_xy(vertex) for vertex in polygon]
    inside = False
    previous = vertices[-1]
    for current in vertices:
        if (current[1] > p[1]) != (previous[1] > p[1]):
            cross_x = current[0] + (p[1] - current[1]) * (previous[0] - current[0]) / (previous[1] - current[1])
            if p[0] < cross_x:
                inside = not inside
        previous = current
    return inside


def rasterize_closed_curve(
    points: Sequence[Any],
    width: int,
    height: int,
    *,
    fill_inside: bool = True,
    island_mask: Sequence[int | bool] | None = None,
) -> bytearray:
    """Rasterize a closed UV polygon using an even/odd scanline fill.

    The result is a row-major byte mask.  ``island_mask`` clips both inside and
    outside fills, preventing a track from leaking into a neighboring UV island.
    """
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("Raster dimensions must be positive")
    polygon = [_xy(point) for point in points]
    if len(polygon) < 3:
        raise ValueError("A closed boundary needs at least three points")
    if island_mask is not None and len(island_mask) != width * height:
        raise ValueError("Island mask size does not match raster dimensions")

    result = bytearray(width * height)
    pixel_polygon = [(u * width, v * height) for u, v in polygon]
    for y in range(height):
        scan_y = y + 0.5
        intersections: list[float] = []
        previous = pixel_polygon[-1]
        for current in pixel_polygon:
            if (current[1] > scan_y) != (previous[1] > scan_y):
                x = current[0] + (scan_y - current[1]) * (previous[0] - current[0]) / (previous[1] - current[1])
                intersections.append(x)
            previous = current
        intersections.sort()
        for pair in range(0, len(intersections) - 1, 2):
            start = max(0, int(floor(intersections[pair] - 0.5)) + 1)
            stop = min(width, int(floor(intersections[pair + 1] - 0.5)) + 1)
            row = y * width
            for x in range(start, stop):
                result[row + x] = 1
    if not fill_inside:
        for index in range(width * height):
            result[index] = 0 if result[index] else 1
    if island_mask is not None:
        for index, allowed in enumerate(island_mask):
            if not allowed:
                result[index] = 0
    return result


def composite_generated_region(
    rgba: MutableSequence[float],
    region: Sequence[int | bool],
    value: float,
) -> None:
    """Apply a generated region while preserving alpha-one paint overrides."""
    if len(rgba) != len(region) * 4:
        raise ValueError("RGBA buffer and region size differ")
    value = max(0.0, min(1.0, float(value)))
    for index, covered in enumerate(region):
        offset = index * 4
        if covered and rgba[offset + 3] < 0.5:
            rgba[offset : offset + 4] = value, value, value, 0.0


def _active_project(scene: Any) -> Any | None:
    projects = getattr(scene, "quick_sdf_projects", None)
    if projects is None:
        projects = getattr(scene, "qsdf_projects", None)
    if not projects:
        return None
    index = int(getattr(scene, "quick_sdf_active_project_index", getattr(scene, "qsdf_active_project_index", 0)))
    return projects[max(0, min(index, len(projects) - 1))]


def _active_track(project: Any) -> Any | None:
    tracks = getattr(project, "boundary_tracks", None)
    if not tracks:
        return None
    index = int(getattr(project, "active_boundary_track_index", 0))
    return tracks[max(0, min(index, len(tracks) - 1))]


def _active_angle(project: Any) -> Any | None:
    angles = getattr(project, "angles", None)
    if not angles:
        return None
    index = int(getattr(project, "active_angle_index", 0))
    return angles[max(0, min(index, len(angles) - 1))]


def _point_assign(point: Any, uv: Vec2) -> None:
    if hasattr(point, "co"):
        point.co = uv
    else:
        point.u, point.v = uv


def _key_for_angle(track: Any, angle: float, create: bool = False) -> Any | None:
    keys = getattr(track, "keys", None)
    if keys is None:
        return None
    for key in keys:
        if abs(float(getattr(key, "angle", 0.0)) - angle) <= 1.0e-4:
            return key
    if not create or not hasattr(keys, "add"):
        return None
    key = keys.add()
    key.angle = angle
    if hasattr(key, "is_manual"):
        key.is_manual = True
    if hasattr(track, "active_key_index"):
        track.active_key_index = len(keys) - 1
    return key


try:  # Blender operator layer -------------------------------------------------
    import bpy
    from bpy.props import BoolProperty, IntProperty
except ImportError:  # pragma: no cover - intentionally supports plain Python
    bpy = None


if bpy is not None:
    def _target_object(project: Any) -> Any | None:
        target = getattr(project, "target_object", None)
        if target is None and getattr(project, "target_object_name", ""):
            target = bpy.data.objects.get(project.target_object_name)
        return target


    def _angle_value(angle_item: Any) -> float:
        return float(getattr(angle_item, "angle", getattr(angle_item, "value", 0.0)))


    def _angle_image(angle_item: Any) -> Any | None:
        image = getattr(angle_item, "image", None)
        if image is None and getattr(angle_item, "image_name", ""):
            image = bpy.data.images.get(angle_item.image_name)
        return image


    def _ensure_track_key(project: Any) -> tuple[Any | None, Any | None]:
        track = _active_track(project)
        if track is None:
            tracks = getattr(project, "boundary_tracks", None)
            if tracks is None or not hasattr(tracks, "add"):
                return None, None
            track = tracks.add()
            if hasattr(track, "name"):
                track.name = f"Boundary {len(tracks)}"
            if hasattr(project, "active_boundary_track_index"):
                project.active_boundary_track_index = len(tracks) - 1
        angle_item = _active_angle(project)
        angle = _angle_value(angle_item) if angle_item is not None else 0.0
        return track, _key_for_angle(track, angle, create=True)


    def _project_uv_boundaries(project: Any) -> list[list[Vec2]]:
        target = _target_object(project)
        if target is None or target.type != "MESH":
            return []
        mesh = target.data
        uv_name = getattr(project, "uv_map_name", "")
        layer = mesh.uv_layers.get(uv_name) if uv_name else mesh.uv_layers.active
        if layer is None:
            return []
        material_index = int(getattr(project, "material_slot_index", 0))
        faces = []
        for polygon in mesh.polygons:
            if polygon.material_index != material_index:
                continue
            faces.append([layer.data[loop_index].uv for loop_index in polygon.loop_indices])
        return uv_boundary_loops(faces)


    def _uv_from_image_event(context: Any, event: Any) -> Vec2 | None:
        image = getattr(context.space_data, "image", None)
        if image is None:
            return None
        x, y = context.region.view2d.region_to_view(event.mouse_region_x, event.mouse_region_y)
        if image.size[0] <= 0 or image.size[1] <= 0:
            return None
        # Image Editor View2D uses normalized image/UV coordinates; Image.size
        # only controls the display aspect and must not scale this conversion.
        return x, y


    def _barycentric_3d(point: Any, a: Any, b: Any, c: Any) -> tuple[float, float, float] | None:
        v0, v1, v2 = b - a, c - a, point - a
        d00, d01, d11 = v0.dot(v0), v0.dot(v1), v1.dot(v1)
        d20, d21 = v2.dot(v0), v2.dot(v1)
        denominator = d00 * d11 - d01 * d01
        if abs(denominator) <= EPSILON:
            return None
        v = (d11 * d20 - d01 * d21) / denominator
        w = (d00 * d21 - d01 * d20) / denominator
        return 1.0 - v - w, v, w


    def _uv_from_view_event(context: Any, event: Any, project: Any) -> Vec2 | None:
        from bpy_extras import view3d_utils

        target = _target_object(project)
        if target is None or target.type != "MESH":
            return None
        coord = event.mouse_region_x, event.mouse_region_y
        origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coord)
        direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coord)
        inverse = target.matrix_world.inverted_safe()
        local_origin = inverse @ origin
        local_direction = (inverse.to_3x3() @ direction).normalized()
        hit, location, _normal, face_index = target.ray_cast(local_origin, local_direction)
        if not hit:
            return None
        mesh = target.data
        uv_name = getattr(project, "uv_map_name", "")
        layer = mesh.uv_layers.get(uv_name) if uv_name else mesh.uv_layers.active
        if layer is None:
            return None
        mesh.calc_loop_triangles()
        for triangle in mesh.loop_triangles:
            if triangle.polygon_index != face_index:
                continue
            vertices = [mesh.vertices[index].co for index in triangle.vertices]
            weights = _barycentric_3d(location, *vertices)
            if weights is None or min(weights) < -1.0e-5:
                continue
            uvs = [layer.data[index].uv for index in triangle.loops]
            return (
                sum(weight * uv.x for weight, uv in zip(weights, uvs)),
                sum(weight * uv.y for weight, uv in zip(weights, uvs)),
            )
        return None


    def _region_for_boundary(
        project: Any,
        track: Any,
        points: Sequence[Any],
        width: int,
        height: int,
        perimeters: Sequence[Sequence[Any]] | None = None,
    ) -> bytearray:
        points = [_xy(point) for point in points]
        fill_inside = getattr(track, "fill_mode", "INSIDE") != "OUTSIDE"
        perimeters = list(perimeters) if perimeters is not None else _project_uv_boundaries(project)
        island_mask = None
        if perimeters:
            island_index = int(getattr(track, "island_index", -1))
            if island_index < 0:
                if bool(getattr(track, "closed", True)):
                    centroid = (
                        sum(point[0] for point in points) / len(points),
                        sum(point[1] for point in points) / len(points),
                    )
                    island_index = next(
                        (index for index, perimeter in enumerate(perimeters) if point_in_polygon(centroid, perimeter)),
                        0,
                    )
                else:
                    island_index = min(
                        range(len(perimeters)),
                        key=lambda index: closest_boundary_index(points[0], perimeters[index])[1]
                        + closest_boundary_index(points[-1], perimeters[index])[1],
                    )
            island_index = max(0, min(island_index, len(perimeters) - 1))
            perimeter = perimeters[island_index]
            island_mask = rasterize_closed_curve(perimeter, width, height)
            if not bool(getattr(track, "closed", True)):
                tolerance = max(0.01, 2.0 / max(width, height))
                points = close_open_curve_along_island(
                    points,
                    perimeter,
                    tolerance=tolerance,
                    prefer_long_arc=not fill_inside,
                )
                fill_inside = True
        elif not bool(getattr(track, "closed", True)):
            raise ValueError("Open boundaries require a UV island perimeter")
        return rasterize_closed_curve(
            points,
            width,
            height,
            fill_inside=fill_inside,
            island_mask=island_mask,
        )


    def commit_boundary_to_image(project: Any, track: Any, key: Any) -> bool:
        """Compatibility helper used by scripts to composite a single key."""
        angle_item = _active_angle(project)
        image = _angle_image(angle_item) if angle_item is not None else None
        if image is None or not getattr(image, "has_data", False):
            return False
        region = _region_for_boundary(project, track, key.points, image.size[0], image.size[1])
        pixels = list(image.pixels[:])
        value = float(getattr(track, "paint_value", 0.0))
        composite_generated_region(pixels, region, value)
        image.pixels.foreach_set(pixels)
        image.update()
        return True


    def regenerate_boundary_images(project: Any, *, allow_violations: bool = True) -> int:
        """Rebuild generated pixels for every angle from all enabled tracks.

        All regions are validated/rasterized before the first Image datablock is
        written.  A bad interpolated curve therefore cannot leave a half-updated
        project. Alpha-one paint overrides are copied through unchanged.
        """
        tracks = [track for track in getattr(project, "boundary_tracks", ()) if getattr(track, "enabled", True)]
        perimeters = _project_uv_boundaries(project)
        import numpy as np

        from . import runtime

        pending: list[tuple[Any, np.ndarray, str, float]] = []
        for angle_item in getattr(project, "angles", ()):
            image = runtime.resolve_display_image(project, angle_item)
            base = runtime.resolve_base_image(project, angle_item)
            coverage = runtime.resolve_coverage_image(project, angle_item)
            if image is None or not getattr(image, "has_data", False):
                continue
            width, height = image.size
            current = runtime.image_rgba(image)
            generated = runtime.image_rgba(base) if base is not None else np.ones_like(current)
            generated[..., 3] = 1.0
            angle = _angle_value(angle_item)
            for track in tracks:
                track_side = str(getattr(track, "side", getattr(angle_item, "side", "RIGHT")))
                if track_side != str(getattr(angle_item, "side", "RIGHT")):
                    continue
                closed = bool(getattr(track, "closed", True))
                points = interpolate_angle_keys(track.keys, angle, closed=closed)
                minimum = 3 if closed else 2
                if len(points) < minimum:
                    continue
                valid, message = validate_curve(points, closed=closed)
                if not valid:
                    raise ValueError(f"{getattr(track, 'name', 'Boundary')}: {message}")
                region = _region_for_boundary(project, track, points, width, height, perimeters)
                area = np.asarray(region, dtype=np.bool_).reshape(height, width)
                # Rasterizer rows follow the image buffer; runtime arrays are
                # top-down, therefore flip once at the layer boundary.
                area = np.flip(area, axis=0)
                value = float(getattr(track, "paint_value", 0.0))
                generated[..., :3][area] = value
            overridden = runtime.coverage_mask(coverage) if coverage is not None else np.zeros((height, width), dtype=np.bool_)
            generated[..., :3][overridden] = current[..., :3][overridden]
            pending.append((image, generated, str(getattr(angle_item, "side", "RIGHT")), angle))
        if not allow_violations and pending:
            from .core import validate_side_monotonic

            for side in ("RIGHT", "LEFT"):
                lane = sorted(
                    ((angle, rgba) for _image, rgba, item_side, angle in pending if item_side == side),
                    key=lambda pair: pair[0],
                )
                if lane:
                    report = validate_side_monotonic(
                        np.stack([rgba[..., 0] >= 0.5 for _angle, rgba in lane]),
                        np.asarray([angle for angle, _rgba in lane]),
                    )
                    if not report.is_valid:
                        raise ValueError(
                            f"This boundary changes {report.violation_pixel_count} pixels in the wrong direction"
                        )
        for image, rgba, _side, _angle in pending:
            runtime.write_image_rgba(image, rgba)
        return len(pending)


    class QSDF_OT_boundary_draw(bpy.types.Operator):
        """Place one boundary key with short-lived clicks in either editor."""

        bl_idname = "quicksdf.boundary_draw"
        bl_label = "Draw Boundary"
        bl_description = "Click points on the mesh or image; Enter commits, Esc cancels"
        bl_options = {"REGISTER", "UNDO", "BLOCKING"}

        _key: Any = None
        _start_count: int = 0

        def invoke(self, context, _event):
            project = _active_project(context.scene)
            if project is None:
                self.report({"ERROR"}, "Create or select a Quick SDF project first")
                return {"CANCELLED"}
            track, key = _ensure_track_key(project)
            if track is None or key is None:
                self.report({"ERROR"}, "Boundary data is unavailable")
                return {"CANCELLED"}
            self._key = key
            self._start_count = len(key.points)
            context.window.cursor_modal_set("CROSSHAIR")
            context.window_manager.modal_handler_add(self)
            context.area.header_text_set("Quick SDF Boundary: LMB add, Backspace remove, Enter finish, Esc cancel")
            return {"RUNNING_MODAL"}

        def _finish(self, context):
            context.window.cursor_modal_restore()
            if context.area:
                context.area.header_text_set(None)

        def modal(self, context, event):
            if event.type == "ESC":
                while len(self._key.points) > self._start_count:
                    self._key.points.remove(len(self._key.points) - 1)
                self._finish(context)
                return {"CANCELLED"}
            if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
                self._finish(context)
                return bpy.ops.quicksdf.boundary_commit(
                    "EXEC_DEFAULT",
                    force=bool(event.alt),
                    rollback_point_count=self._start_count,
                )
            if event.type == "BACK_SPACE" and event.value == "PRESS":
                if len(self._key.points) > self._start_count:
                    self._key.points.remove(len(self._key.points) - 1)
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                project = _active_project(context.scene)
                uv = None
                if context.area.type == "IMAGE_EDITOR":
                    uv = _uv_from_image_event(context, event)
                elif context.area.type == "VIEW_3D":
                    uv = _uv_from_view_event(context, event, project)
                if uv is None:
                    self.report({"WARNING"}, "No UV surface under cursor")
                    return {"RUNNING_MODAL"}
                if not (0.0 <= uv[0] <= 1.0 and 0.0 <= uv[1] <= 1.0):
                    self.report({"WARNING"}, "Point is outside the 0-1 UV tile")
                    return {"RUNNING_MODAL"}
                point = self._key.points.add()
                _point_assign(point, uv)
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        def cancel(self, context):
            self._finish(context)




    class QSDF_OT_boundary_commit(bpy.types.Operator):
        bl_idname = "quicksdf.boundary_commit"
        bl_label = "Commit Boundary Key"
        bl_description = "Validate and rasterize the active boundary while preserving paint overrides"
        bl_options = {"REGISTER", "UNDO"}

        force: BoolProperty(
            name="Keep Monotonic Violation",
            description="Keep this boundary even if project validation reports a monotonic violation",
            default=False,
        )
        rollback_point_count: IntProperty(default=-1, options={"HIDDEN", "SKIP_SAVE"})

        def execute(self, context):
            project = _active_project(context.scene)
            track = _active_track(project) if project is not None else None
            angle_item = _active_angle(project) if project is not None else None
            angle = _angle_value(angle_item) if angle_item is not None else 0.0
            key = _key_for_angle(track, angle) if track is not None else None
            if project is None or track is None or key is None:
                self.report({"ERROR"}, "No active boundary key")
                return {"CANCELLED"}
            closed = bool(getattr(track, "closed", True))
            valid, message = validate_curve(key.points, closed=closed)
            if not valid:
                if self.rollback_point_count >= 0:
                    while len(key.points) > self.rollback_point_count:
                        key.points.remove(len(key.points) - 1)
                self.report({"ERROR"}, message)
                return {"CANCELLED"}
            if hasattr(key, "is_manual"):
                key.is_manual = True
            if hasattr(project, "dirty"):
                project.dirty = True
            try:
                updated = regenerate_boundary_images(project, allow_violations=self.force)
            except ValueError as exc:
                if self.rollback_point_count >= 0:
                    while len(key.points) > self.rollback_point_count:
                        key.points.remove(len(key.points) - 1)
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if angle_item is not None:
                if hasattr(angle_item, "is_manual"):
                    angle_item.is_manual = True
                if hasattr(angle_item, "is_generated"):
                    angle_item.is_generated = False
            try:
                from .runtime import validate_project

                validate_project(project, include_monotonic=True)
            except (RuntimeError, ValueError):
                pass
            for area in context.screen.areas if context.screen else ():
                if area.type in {"VIEW_3D", "IMAGE_EDITOR"}:
                    area.tag_redraw()
            self.report({"INFO"}, f"Boundary key committed at {angle:g} degrees; updated {updated} masks")
            return {"FINISHED"}

        def invoke(self, context, event):
            self.force = bool(event.alt)
            return self.execute(context)


    class QSDF_OT_boundary_clear_key(bpy.types.Operator):
        bl_idname = "quicksdf.boundary_clear_key"
        bl_label = "Clear Boundary Key"
        bl_description = "Remove the active angle key from this boundary track"
        bl_options = {"REGISTER", "UNDO"}

        def execute(self, context):
            project = _active_project(context.scene)
            track = _active_track(project) if project is not None else None
            angle_item = _active_angle(project) if project is not None else None
            if track is None or angle_item is None:
                return {"CANCELLED"}
            angle = _angle_value(angle_item)
            for index, key in enumerate(track.keys):
                if abs(float(key.angle) - angle) <= 1.0e-4:
                    track.keys.remove(index)
                    try:
                        regenerate_boundary_images(project)
                        from .runtime import validate_project

                        validate_project(project, include_monotonic=True)
                    except (RuntimeError, ValueError) as exc:
                        self.report({"WARNING"}, str(exc))
                    manual_angles = {
                        round(float(boundary_key.angle), 4)
                        for boundary_track in project.boundary_tracks
                        for boundary_key in boundary_track.keys
                        if getattr(boundary_key, "is_manual", True)
                    }
                    for item in project.angles:
                        item.is_manual = round(float(item.angle), 4) in manual_angles
                        item.is_generated = not item.is_manual
                    project.dirty = True
                    return {"FINISHED"}
            self.report({"INFO"}, "The current angle is interpolated; it has no key to remove")
            return {"CANCELLED"}


    CLASSES = (
        QSDF_OT_boundary_draw,
        QSDF_OT_boundary_commit,
        QSDF_OT_boundary_clear_key,
    )
else:
    CLASSES: tuple[type, ...] = ()


__all__ = [
    "BezierKnot",
    "CLASSES",
    "close_open_curve_along_island",
    "composite_generated_region",
    "cubic_bezier",
    "curve_self_intersects",
    "interpolate_angle_keys",
    "interpolate_curves",
    "polygon_signed_area",
    "point_in_polygon",
    "rasterize_closed_curve",
    "regenerate_boundary_images",
    "resample_polyline",
    "sample_bezier",
    "segments_intersect",
    "uv_boundary_loops",
    "validate_curve",
]
