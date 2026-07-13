// SPDX-License-Identifier: GPL-3.0-or-later
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <new>
#include <thread>
#include <vector>

#if defined(_WIN32)
#  define QSDF_API extern "C" __declspec(dllexport)
#else
#  define QSDF_API extern "C" __attribute__((visibility("default")))
#endif

namespace {

constexpr int QSDF_OK = 0;
constexpr int QSDF_INVALID_ARGUMENT = 1;
constexpr int QSDF_NON_MONOTONIC = 2;
constexpr int QSDF_OUT_OF_MEMORY = 3;
constexpr int QSDF_CANCELLED = 4;
constexpr double QSDF_PI = 3.1415926535897932384626433832795;

struct Vec3 {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

Vec3 add_scaled(Vec3 value, const float* source, double scale) {
    value.x += double(source[0]) * scale;
    value.y += double(source[1]) * scale;
    value.z += double(source[2]) * scale;
    return value;
}

double length(Vec3 value) {
    return std::sqrt(value.x * value.x + value.y * value.y + value.z * value.z);
}

bool normalize(Vec3& value) {
    const double magnitude = length(value);
    if (!std::isfinite(magnitude) || magnitude <= 1.0e-12) return false;
    value.x /= magnitude;
    value.y /= magnitude;
    value.z /= magnitude;
    return true;
}

double dot(Vec3 first, Vec3 second) {
    return first.x * second.x + first.y * second.y + first.z * second.z;
}

Vec3 cross(Vec3 first, Vec3 second) {
    return {
        first.y * second.z - first.z * second.y,
        first.z * second.x - first.x * second.z,
        first.x * second.y - first.y * second.x,
    };
}

/*
 * The parabolic lower-envelope distance transform below is adapted from the
 * implementation accompanying "Distance Transforms of Sampled Functions".
 * Copyright (C) 2006 Pedro Felzenszwalb.
 * Original code: GPL-2.0-or-later.
 * Source: https://cs.brown.edu/people/pfelzens/dt/
 * Quick SDF modifications: GPL-3.0-or-later; see THIRD_PARTY_NOTICES.md.
 */
void dt_1d(const double* f, double* d, int n, std::vector<int>& v,
           std::vector<double>& z) {
    int k = 0;
    v[0] = 0;
    z[0] = -std::numeric_limits<double>::infinity();
    z[1] = std::numeric_limits<double>::infinity();
    for (int q = 1; q < n; ++q) {
        double s = 0.0;
        for (;;) {
            const int vk = v[k];
            s = ((f[q] + double(q) * q) - (f[vk] + double(vk) * vk)) /
                (2.0 * (q - vk));
            if (s > z[k] || k == 0) {
                break;
            }
            --k;
        }
        ++k;
        v[k] = q;
        z[k] = s;
        z[k + 1] = std::numeric_limits<double>::infinity();
    }
    k = 0;
    for (int q = 0; q < n; ++q) {
        while (z[k + 1] < q) {
            ++k;
        }
        const double delta = double(q - v[k]);
        d[q] = delta * delta + f[v[k]];
    }
}

bool dt_2d(std::vector<double>& grid, int width, int height,
           const int* cancel_requested = nullptr) {
    const int max_dim = std::max(width, height);
    std::vector<double> f(max_dim), d(max_dim), z(max_dim + 1);
    std::vector<int> v(max_dim);
    for (int y = 0; y < height; ++y) {
        if (cancel_requested && *cancel_requested) return false;
        for (int x = 0; x < width; ++x) f[x] = grid[size_t(y) * width + x];
        dt_1d(f.data(), d.data(), width, v, z);
        for (int x = 0; x < width; ++x) grid[size_t(y) * width + x] = d[x];
    }
    for (int x = 0; x < width; ++x) {
        if (cancel_requested && *cancel_requested) return false;
        for (int y = 0; y < height; ++y) f[y] = grid[size_t(y) * width + x];
        dt_1d(f.data(), d.data(), height, v, z);
        for (int y = 0; y < height; ++y) grid[size_t(y) * width + x] = d[y];
    }
    for (size_t index = 0; index < grid.size(); ++index) {
        if ((index & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        grid[index] = std::sqrt(std::max(0.0, grid[index]));
    }
    return true;
}

bool signed_distance(const uint8_t* light_mask, int width, int height,
                     std::vector<float>& output,
                     const int* cancel_requested = nullptr) {
    const size_t pixels = size_t(width) * height;
    const double far_sq = double(width) * width + double(height) * height + 100.0;
    std::vector<double> to_shadow(pixels), to_light(pixels);
    bool has_light = false;
    bool has_shadow = false;
    for (size_t p = 0; p < pixels; ++p) {
        if ((p & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        const bool light = light_mask[p] != 0;
        has_light |= light;
        has_shadow |= !light;
        to_shadow[p] = light ? far_sq : 0.0;
        to_light[p] = light ? 0.0 : far_sq;
    }
    output.resize(pixels);
    if (!has_shadow) {
        std::fill(output.begin(), output.end(), -std::numeric_limits<float>::infinity());
        return true;
    }
    if (!has_light) {
        std::fill(output.begin(), output.end(), std::numeric_limits<float>::infinity());
        return true;
    }
    if (!dt_2d(to_shadow, width, height, cancel_requested) ||
        !dt_2d(to_light, width, height, cancel_requested))
        return false;
    for (size_t p = 0; p < pixels; ++p) {
        if ((p & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        // Positive is shadow, negative is light, matching QuickSDF's convention.
        output[p] = float(to_light[p] - to_shadow[p]);
    }
    return true;
}

uint16_t encode_transition(float normalized_angle) {
    normalized_angle = std::clamp(normalized_angle, 0.0f, 1.0f);
    return uint16_t(1 + std::lround(normalized_angle * 65533.0f));
}

int validate_sequence(const uint8_t* masks, const std::vector<int>& sequence,
                      size_t pixels, const int* cancel_requested = nullptr) {
    int violations = 0;
    for (size_t p = 0; p < pixels; ++p) {
        if ((p & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return -1;
        bool was_light = masks[size_t(sequence.front()) * pixels + p] != 0;
        for (size_t i = 1; i < sequence.size(); ++i) {
            const bool light = masks[size_t(sequence[i]) * pixels + p] != 0;
            if (was_light && !light) {
                ++violations;
                break;
            }
            was_light = light;
        }
    }
    return violations;
}

bool generate_channel(const uint8_t* masks, const float* angles,
                      const std::vector<int>& sequence, int width, int height,
                      uint16_t* output, int stride,
                      const int* cancel_requested = nullptr) {
    const size_t pixels = size_t(width) * height;
    const uint8_t* first = masks + size_t(sequence.front()) * pixels;
    for (size_t p = 0; p < pixels; ++p) {
        if ((p & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        output[p * stride] = first[p] ? 0 : 65535;
    }
    if (sequence.size() < 2) return true;

    std::vector<float> previous_sdf, current_sdf;
    if (!signed_distance(first, width, height, previous_sdf, cancel_requested))
        return false;
    for (size_t i = 1; i < sequence.size(); ++i) {
        if (cancel_requested && *cancel_requested) return false;
        const int previous_index = sequence[i - 1];
        const int current_index = sequence[i];
        const uint8_t* previous = masks + size_t(previous_index) * pixels;
        const uint8_t* current = masks + size_t(current_index) * pixels;
        if (!signed_distance(current, width, height, current_sdf, cancel_requested))
            return false;
        const float a0 = std::abs(angles[previous_index]) / 90.0f;
        const float a1 = std::abs(angles[current_index]) / 90.0f;
        for (size_t p = 0; p < pixels; ++p) {
            if (previous[p] == 0 && current[p] != 0) {
                const float d0 = std::abs(previous_sdf[p]);
                const float d1 = std::abs(current_sdf[p]);
                const float denominator = d0 + d1;
                const float ratio = std::isfinite(denominator) && denominator > 0.0f
                                        ? d0 / denominator
                                        : 0.5f;
                output[p * stride] = encode_transition(a0 + (a1 - a0) * ratio);
            }
        }
        previous_sdf.swap(current_sdf);
    }
    return true;
}

bool build_side_sequence(const float* angles, int count,
                         std::vector<int>& sequence) {
    if (!angles || count < 2) return false;
    sequence.resize(size_t(count));
    for (int i = 0; i < count; ++i) {
        if (!std::isfinite(angles[i]) || angles[i] < -1.0e-5f ||
            angles[i] > 90.0f + 1.0e-5f)
            return false;
        sequence[size_t(i)] = i;
    }
    std::stable_sort(sequence.begin(), sequence.end(),
                     [angles](int first, int second) {
                         return angles[first] < angles[second];
                     });
    if (std::abs(angles[sequence.front()]) > 1.0e-5f ||
        std::abs(angles[sequence.back()] - 90.0f) > 1.0e-5f)
        return false;
    for (int i = 1; i < count; ++i)
        if (angles[sequence[size_t(i)]] - angles[sequence[size_t(i - 1)]] <=
            1.0e-5f)
            return false;
    return true;
}

bool build_bake_directions(const float* angles, int count, const float* forward,
                           const float* up, std::vector<Vec3>& directions,
                           int& zero_index) {
    if (!angles || !forward || !up || count < 1) return false;
    Vec3 up_axis{up[0], up[1], up[2]};
    Vec3 front{forward[0], forward[1], forward[2]};
    if (!normalize(up_axis) || !normalize(front)) return false;
    const double vertical = dot(front, up_axis);
    front.x -= up_axis.x * vertical;
    front.y -= up_axis.y * vertical;
    front.z -= up_axis.z * vertical;
    if (!normalize(front)) return false;
    const Vec3 tangent = cross(up_axis, front);
    directions.resize(size_t(count));
    zero_index = -1;
    for (int index = 0; index < count; ++index) {
        const float angle = angles[index];
        if (!std::isfinite(angle) || angle < -90.00001f || angle > 90.00001f)
            return false;
        if (std::abs(angle) <= 1.0e-5f) {
            if (zero_index >= 0) return false;
            zero_index = index;
        }
        for (int other = 0; other < index; ++other)
            if (std::abs(angles[other] - angle) <= 1.0e-5f) return false;
        const double radians = double(angle) * QSDF_PI / 180.0;
        Vec3 direction{
            front.x * std::cos(radians) + tangent.x * std::sin(radians),
            front.y * std::cos(radians) + tangent.y * std::sin(radians),
            front.z * std::cos(radians) + tangent.z * std::sin(radians),
        };
        if (!normalize(direction)) return false;
        directions[size_t(index)] = direction;
    }
    return zero_index >= 0;
}

void enforce_bake_monotonic(uint8_t* masks, const float* angles, int count,
                            int zero_index, size_t pixels) {
    for (int sign : {-1, 1}) {
        std::vector<int> sequence{zero_index};
        for (int index = 0; index < count; ++index)
            if (angles[index] * float(sign) > 1.0e-5f) sequence.push_back(index);
        std::stable_sort(sequence.begin() + 1, sequence.end(), [angles](int a, int b) {
            return std::abs(angles[a]) < std::abs(angles[b]);
        });
        for (size_t step = 1; step < sequence.size(); ++step) {
            const uint8_t* previous = masks + size_t(sequence[step - 1]) * pixels;
            uint8_t* current = masks + size_t(sequence[step]) * pixels;
            for (size_t pixel = 0; pixel < pixels; ++pixel)
                current[pixel] = uint8_t(current[pixel] || previous[pixel]);
        }
    }
}

bool build_guide_directions(const float* angles, int count, const float* forward,
                            const float* up, int side_sign,
                            std::vector<Vec3>& directions) {
    if (!angles || !forward || !up || count < 2 ||
        (side_sign != 1 && side_sign != -1))
        return false;
    Vec3 up_vector{up[0], up[1], up[2]};
    Vec3 front{forward[0], forward[1], forward[2]};
    if (!normalize(up_vector) || !normalize(front)) return false;
    const double vertical = dot(front, up_vector);
    front.x -= up_vector.x * vertical;
    front.y -= up_vector.y * vertical;
    front.z -= up_vector.z * vertical;
    if (!normalize(front)) return false;
    Vec3 side = cross(up_vector, front);
    if (!normalize(side)) return false;
    side.x *= side_sign;
    side.y *= side_sign;
    side.z *= side_sign;
    if (std::abs(angles[0]) > 1.0e-5f ||
        std::abs(angles[count - 1] - 90.0f) > 1.0e-5f)
        return false;
    directions.resize(size_t(count));
    for (int index = 0; index < count; ++index) {
        const float angle = angles[index];
        if (!std::isfinite(angle) || angle < -1.0e-5f || angle > 90.0f + 1.0e-5f ||
            (index > 0 && angle - angles[index - 1] <= 1.0e-5f))
            return false;
        const double radians = double(angle) * QSDF_PI / 180.0;
        Vec3 direction{
            side.x * std::cos(radians) + front.x * std::sin(radians),
            side.y * std::cos(radians) + front.y * std::sin(radians),
            side.z * std::cos(radians) + front.z * std::sin(radians),
        };
        if (!normalize(direction)) return false;
        directions[size_t(index)] = direction;
    }
    return true;
}

int rasterize_guide_normals(const float* triangle_uvs, const float* corner_normals,
                            int triangle_count, int width, int height,
                            std::vector<Vec3>& normal_image,
                            uint8_t* output_occupancy,
                            const int* cancel_requested) {
    const size_t pixels = size_t(width) * size_t(height);
    normal_image.assign(pixels, Vec3{});
    std::fill(output_occupancy, output_occupancy + pixels, uint8_t(0));
    for (int triangle = 0; triangle < triangle_count; ++triangle) {
        if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
        const float* uv = triangle_uvs + size_t(triangle) * 6;
        const float* normal = corner_normals + size_t(triangle) * 9;
        double x[3], y[3];
        for (int corner = 0; corner < 3; ++corner) {
            x[corner] = double(uv[corner * 2]) * width - 0.5;
            y[corner] = (1.0 - double(uv[corner * 2 + 1])) * height - 0.5;
        }
        const int x0 = std::max(0, int(std::ceil(std::min({x[0], x[1], x[2]}))));
        const int x1 = std::min(width - 1, int(std::floor(std::max({x[0], x[1], x[2]}))));
        const int y0 = std::max(0, int(std::ceil(std::min({y[0], y[1], y[2]}))));
        const int y1 = std::min(height - 1, int(std::floor(std::max({y[0], y[1], y[2]}))));
        if (x1 < x0 || y1 < y0) continue;
        const double denominator =
            (y[1] - y[2]) * (x[0] - x[2]) +
            (x[2] - x[1]) * (y[0] - y[2]);
        if (std::abs(denominator) <= 1.0e-12) continue;
        for (int row = y0; row <= y1; ++row) {
            for (int column = x0; column <= x1; ++column) {
                const double w0 =
                    ((y[1] - y[2]) * (column - x[2]) +
                     (x[2] - x[1]) * (row - y[2])) /
                    denominator;
                const double w1 =
                    ((y[2] - y[0]) * (column - x[2]) +
                     (x[0] - x[2]) * (row - y[2])) /
                    denominator;
                const double w2 = 1.0 - w0 - w1;
                if (w0 < -1.0e-9 || w1 < -1.0e-9 || w2 < -1.0e-9) continue;
                Vec3 value{};
                value = add_scaled(value, normal, w0);
                value = add_scaled(value, normal + 3, w1);
                value = add_scaled(value, normal + 6, w2);
                if (!normalize(value)) {
                    int dominant = 0;
                    if (w1 > w0) dominant = 1;
                    if (w2 > (dominant == 0 ? w0 : w1)) dominant = 2;
                    value = {normal[dominant * 3], normal[dominant * 3 + 1],
                             normal[dominant * 3 + 2]};
                    if (!normalize(value)) return QSDF_INVALID_ARGUMENT;
                }
                const size_t pixel = size_t(row) * width + column;
                normal_image[pixel] = value;
                output_occupancy[pixel] = 1;
            }
        }
    }
    return QSDF_OK;
}

}  // namespace

QSDF_API int qsdf_version() { return 4; }

QSDF_API int qsdf_bake_normal_sweep(
    const float* triangle_uvs, const float* corner_normals, int triangle_count,
    const float* angles, int angle_count, const float* forward, const float* up,
    int width, int height, uint8_t* output_masks, uint8_t* output_occupancy,
    const int* cancel_requested) {
    if (!triangle_uvs || !corner_normals || !angles || !forward || !up ||
        !output_masks || !output_occupancy || triangle_count < 0 ||
        angle_count < 1 || width < 1 || height < 1)
        return QSDF_INVALID_ARGUMENT;
    try {
        const size_t pixels = size_t(width) * size_t(height);
        std::vector<Vec3> normal_image(pixels);
        std::fill(output_occupancy, output_occupancy + pixels, uint8_t(0));
        for (int triangle = 0; triangle < triangle_count; ++triangle) {
            if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
            const float* uv = triangle_uvs + size_t(triangle) * 6;
            const float* normal = corner_normals + size_t(triangle) * 9;
            double x[3], y[3];
            for (int corner = 0; corner < 3; ++corner) {
                x[corner] = double(uv[corner * 2]) * width - 0.5;
                y[corner] = (1.0 - double(uv[corner * 2 + 1])) * height - 0.5;
            }
            const int x0 = std::max(0, int(std::ceil(std::min({x[0], x[1], x[2]}))));
            const int x1 = std::min(width - 1, int(std::floor(std::max({x[0], x[1], x[2]}))));
            const int y0 = std::max(0, int(std::ceil(std::min({y[0], y[1], y[2]}))));
            const int y1 = std::min(height - 1, int(std::floor(std::max({y[0], y[1], y[2]}))));
            if (x1 < x0 || y1 < y0) continue;
            const double denominator =
                (y[1] - y[2]) * (x[0] - x[2]) +
                (x[2] - x[1]) * (y[0] - y[2]);
            if (std::abs(denominator) <= 1.0e-12) continue;
            for (int row = y0; row <= y1; ++row) {
                for (int column = x0; column <= x1; ++column) {
                    const double w0 =
                        ((y[1] - y[2]) * (column - x[2]) +
                         (x[2] - x[1]) * (row - y[2])) /
                        denominator;
                    const double w1 =
                        ((y[2] - y[0]) * (column - x[2]) +
                         (x[0] - x[2]) * (row - y[2])) /
                        denominator;
                    const double w2 = 1.0 - w0 - w1;
                    if (w0 < -1.0e-9 || w1 < -1.0e-9 || w2 < -1.0e-9) continue;
                    Vec3 value{};
                    value = add_scaled(value, normal, w0);
                    value = add_scaled(value, normal + 3, w1);
                    value = add_scaled(value, normal + 6, w2);
                    if (!normalize(value)) {
                        int dominant = 0;
                        if (w1 > w0) dominant = 1;
                        if (w2 > (dominant == 0 ? w0 : w1)) dominant = 2;
                        value = {normal[dominant * 3], normal[dominant * 3 + 1],
                                 normal[dominant * 3 + 2]};
                        if (!normalize(value)) return QSDF_INVALID_ARGUMENT;
                    }
                    const size_t pixel = size_t(row) * width + column;
                    normal_image[pixel] = value;
                    output_occupancy[pixel] = 1;
                }
            }
        }
        std::vector<Vec3> directions;
        int zero_index = -1;
        if (!build_bake_directions(angles, angle_count, forward, up, directions,
                                   zero_index))
            return QSDF_INVALID_ARGUMENT;
        for (int angle = 0; angle < angle_count; ++angle) {
            if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
            uint8_t* mask = output_masks + size_t(angle) * pixels;
            const Vec3 direction = directions[size_t(angle)];
            for (size_t pixel = 0; pixel < pixels; ++pixel)
                mask[pixel] = uint8_t(!output_occupancy[pixel] ||
                                      dot(normal_image[pixel], direction) >= 0.0);
        }
        enforce_bake_monotonic(output_masks, angles, angle_count, zero_index, pixels);
        return QSDF_OK;
    } catch (const std::bad_alloc&) {
        return QSDF_OUT_OF_MEMORY;
    }
}

QSDF_API int qsdf_bake_face_shadow_guide(
    const float* triangle_uvs, const float* corner_normals, int triangle_count,
    const float* angles, int angle_count, const float* forward, const float* up,
    int side_sign, float cutoff, int width, int height, uint8_t* output_masks,
    uint8_t* output_occupancy, const int* cancel_requested) {
    if (!triangle_uvs || !corner_normals || !angles || !forward || !up ||
        !output_masks || !output_occupancy || triangle_count < 0 ||
        angle_count < 2 || width < 1 || height < 1 || !std::isfinite(cutoff))
        return QSDF_INVALID_ARGUMENT;
    try {
        const size_t pixels = size_t(width) * size_t(height);
        std::vector<Vec3> normal_image;
        const int rasterized = rasterize_guide_normals(
            triangle_uvs, corner_normals, triangle_count, width, height,
            normal_image, output_occupancy, cancel_requested);
        if (rasterized != QSDF_OK) return rasterized;
        std::vector<Vec3> directions;
        if (!build_guide_directions(angles, angle_count, forward, up, side_sign,
                                    directions))
            return QSDF_INVALID_ARGUMENT;
        for (int angle = 0; angle < angle_count; ++angle) {
            if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
            uint8_t* mask = output_masks + size_t(angle) * pixels;
            const Vec3 direction = directions[size_t(angle)];
            for (size_t pixel = 0; pixel < pixels; ++pixel)
                mask[pixel] = uint8_t(!output_occupancy[pixel] ||
                                      dot(normal_image[pixel], direction) >= cutoff);
        }
        for (int angle = 1; angle < angle_count; ++angle) {
            const uint8_t* previous = output_masks + size_t(angle - 1) * pixels;
            uint8_t* current = output_masks + size_t(angle) * pixels;
            for (size_t pixel = 0; pixel < pixels; ++pixel)
                current[pixel] = uint8_t(current[pixel] || previous[pixel]);
        }
        return QSDF_OK;
    } catch (const std::bad_alloc&) {
        return QSDF_OUT_OF_MEMORY;
    }
}

QSDF_API int qsdf_repair_side_monotonic(
    const uint8_t* masks, const uint8_t* base_masks,
    const uint8_t* coverage_masks, int count, int width, int height,
    uint8_t* output_masks, uint8_t* output_changed, int32_t* output_transition,
    int* changed_samples, int* changed_pixels,
    int* protected_changed_samples, int* protected_changed_pixels,
    const int* cancel_requested) {
    if (!masks || !base_masks || !coverage_masks || !output_masks ||
        !output_changed || !output_transition || !changed_samples ||
        !changed_pixels || !protected_changed_samples ||
        !protected_changed_pixels || count < 1 || width < 1 || height < 1)
        return QSDF_INVALID_ARGUMENT;
    if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
    const size_t pixels = size_t(width) * size_t(height);
    struct RepairCounts {
        int changed_samples = 0;
        int changed_pixels = 0;
        int protected_changed_samples = 0;
        int protected_changed_pixels = 0;
    };
    auto repair_range = [&](size_t begin, size_t end, RepairCounts& counts) {
      for (size_t pixel = begin; pixel < end; ++pixel) {
        if ((pixel & size_t(4095)) == 0 && cancel_requested && *cancel_requested)
            return;
        int protected_cost = 0;
        int display_cost = 0;
        int base_cost = 0;
        for (int sample = 0; sample < count; ++sample) {
            const size_t offset = size_t(sample) * pixels + pixel;
            const bool display = masks[offset] != 0;
            const bool base = base_masks[offset] != 0;
            const bool protected_value = coverage_masks[offset] != 0 || display != base;
            if (!display) {
                ++display_cost;
                if (protected_value) ++protected_cost;
            }
            if (!base) ++base_cost;
        }
        int best_protected = protected_cost;
        int best_display = display_cost;
        int best_base = base_cost;
        int best_transition = 0;
        for (int transition = 1; transition <= count; ++transition) {
            const int sample = transition - 1;
            const size_t offset = size_t(sample) * pixels + pixel;
            const bool display = masks[offset] != 0;
            const bool base = base_masks[offset] != 0;
            const bool protected_value = coverage_masks[offset] != 0 || display != base;
            const int display_delta = display ? 1 : -1;
            protected_cost += protected_value ? display_delta : 0;
            display_cost += display_delta;
            base_cost += base ? 1 : -1;
            if (protected_cost < best_protected ||
                (protected_cost == best_protected &&
                 (display_cost < best_display ||
                  (display_cost == best_display && base_cost < best_base)))) {
                best_protected = protected_cost;
                best_display = display_cost;
                best_base = base_cost;
                best_transition = transition;
            }
        }
        output_transition[pixel] = best_transition;
        bool pixel_changed = false;
        bool protected_pixel_changed = false;
        for (int sample = 0; sample < count; ++sample) {
            const size_t offset = size_t(sample) * pixels + pixel;
            const bool display = masks[offset] != 0;
            const bool base = base_masks[offset] != 0;
            const bool repaired = sample >= best_transition;
            const bool changed = repaired != display;
            const bool protected_value = coverage_masks[offset] != 0 || display != base;
            output_masks[offset] = repaired ? 1 : 0;
            output_changed[offset] = changed ? 1 : 0;
            if (changed) {
                ++counts.changed_samples;
                pixel_changed = true;
                if (protected_value) {
                    ++counts.protected_changed_samples;
                    protected_pixel_changed = true;
                }
            }
        }
        if (pixel_changed) ++counts.changed_pixels;
        if (protected_pixel_changed) ++counts.protected_changed_pixels;
      }
    };

    const size_t minimum_pixels_per_worker = 65536;
    const size_t useful_workers =
        (pixels + minimum_pixels_per_worker - 1) / minimum_pixels_per_worker;
    const size_t worker_count = std::max<size_t>(
        1, std::min<size_t>({size_t(16), useful_workers,
                            size_t(std::max(1u, std::thread::hardware_concurrency()))}));
    std::vector<RepairCounts> counts(worker_count);
    std::vector<std::thread> threads;
    try {
        threads.reserve(worker_count > 0 ? worker_count - 1 : 0);
        for (size_t worker = 1; worker < worker_count; ++worker) {
            const size_t begin = pixels * worker / worker_count;
            const size_t end = pixels * (worker + 1) / worker_count;
            threads.emplace_back(repair_range, begin, end, std::ref(counts[worker]));
        }
        repair_range(0, pixels / worker_count, counts[0]);
        for (auto& thread : threads) thread.join();
    } catch (...) {
        for (auto& thread : threads) {
            if (thread.joinable()) thread.join();
        }
        return QSDF_OUT_OF_MEMORY;
    }
    if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
    *changed_samples = 0;
    *changed_pixels = 0;
    *protected_changed_samples = 0;
    *protected_changed_pixels = 0;
    for (const auto& value : counts) {
        *changed_samples += value.changed_samples;
        *changed_pixels += value.changed_pixels;
        *protected_changed_samples += value.protected_changed_samples;
        *protected_changed_pixels += value.protected_changed_pixels;
    }
    return QSDF_OK;
}

QSDF_API int qsdf_validate_monotonic(const uint8_t* masks, const float* angles,
                                     int count, int width, int height,
                                     int* violation_pixels) {
    if (!masks || !angles || !violation_pixels || count < 1 || width < 1 || height < 1)
        return QSDF_INVALID_ARGUMENT;
    int zero = 0;
    for (int i = 1; i < count; ++i)
        if (std::abs(angles[i]) < std::abs(angles[zero])) zero = i;
    std::vector<int> positive{zero}, negative{zero};
    for (int i = zero + 1; i < count; ++i) if (angles[i] > angles[zero]) positive.push_back(i);
    for (int i = zero - 1; i >= 0; --i) if (angles[i] < angles[zero]) negative.push_back(i);
    const size_t pixels = size_t(width) * height;
    *violation_pixels = validate_sequence(masks, positive, pixels) +
                        validate_sequence(masks, negative, pixels);
    return *violation_pixels ? QSDF_NON_MONOTONIC : QSDF_OK;
}

QSDF_API int qsdf_generate_threshold(const uint8_t* masks, const float* angles,
                                     int count, int width, int height,
                                     uint16_t* output_rg, int* violation_pixels) {
    if (!masks || !angles || !output_rg || !violation_pixels || count < 1 || width < 1 || height < 1)
        return QSDF_INVALID_ARGUMENT;
    try {
        const int valid = qsdf_validate_monotonic(masks, angles, count, width, height,
                                                   violation_pixels);
        if (valid != QSDF_OK) return valid;
        int zero = 0;
        for (int i = 1; i < count; ++i)
            if (std::abs(angles[i]) < std::abs(angles[zero])) zero = i;
        std::vector<int> positive{zero}, negative{zero};
        for (int i = zero + 1; i < count; ++i) if (angles[i] > angles[zero]) positive.push_back(i);
        for (int i = zero - 1; i >= 0; --i) if (angles[i] < angles[zero]) negative.push_back(i);
        if (!generate_channel(masks, angles, positive, width, height, output_rg, 2) ||
            !generate_channel(masks, angles, negative, width, height, output_rg + 1, 2))
            return QSDF_CANCELLED;
        return QSDF_OK;
    } catch (const std::bad_alloc&) {
        return QSDF_OUT_OF_MEMORY;
    }
}

QSDF_API int qsdf_generate_threshold_pair_cancelable(
    const uint8_t* right_masks, const float* right_angles, int right_count,
    const uint8_t* left_masks, const float* left_angles, int left_count,
    int width, int height, uint16_t* output_rg,
    int* right_violation_pixels, int* left_violation_pixels,
    const int* cancel_requested) {
    if (!right_masks || !right_angles || !left_masks || !left_angles ||
        !output_rg || !right_violation_pixels || !left_violation_pixels ||
        right_count < 2 || left_count < 2 || width < 1 || height < 1)
        return QSDF_INVALID_ARGUMENT;
    try {
        if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
        std::vector<int> right_sequence, left_sequence;
        if (!build_side_sequence(right_angles, right_count, right_sequence) ||
            !build_side_sequence(left_angles, left_count, left_sequence))
            return QSDF_INVALID_ARGUMENT;
        const size_t pixels = size_t(width) * height;
        *right_violation_pixels =
            validate_sequence(right_masks, right_sequence, pixels, cancel_requested);
        if (*right_violation_pixels < 0) return QSDF_CANCELLED;
        *left_violation_pixels =
            validate_sequence(left_masks, left_sequence, pixels, cancel_requested);
        if (*left_violation_pixels < 0) return QSDF_CANCELLED;
        if (*right_violation_pixels || *left_violation_pixels)
            return QSDF_NON_MONOTONIC;
        if (!generate_channel(right_masks, right_angles, right_sequence, width, height,
                              output_rg, 2, cancel_requested) ||
            !generate_channel(left_masks, left_angles, left_sequence, width, height,
                              output_rg + 1, 2, cancel_requested))
            return QSDF_CANCELLED;
        return QSDF_OK;
    } catch (const std::bad_alloc&) {
        return QSDF_OUT_OF_MEMORY;
    }
}

QSDF_API int qsdf_generate_threshold_pair(
    const uint8_t* right_masks, const float* right_angles, int right_count,
    const uint8_t* left_masks, const float* left_angles, int left_count,
    int width, int height, uint16_t* output_rg,
    int* right_violation_pixels, int* left_violation_pixels) {
    return qsdf_generate_threshold_pair_cancelable(
        right_masks, right_angles, right_count,
        left_masks, left_angles, left_count,
        width, height, output_rg,
        right_violation_pixels, left_violation_pixels, nullptr);
}
