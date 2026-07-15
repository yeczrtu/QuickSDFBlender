import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { normalizedThresholdBlur } from "../scripts/threshold-study-math.mjs";

const websiteRoot = resolve(import.meta.dirname, "..");
const generator = resolve(websiteRoot, "scripts/generate-threshold-study.mjs");

test("keeps valid Box Blur values independent of the always-Shadow sentinel", () => {
  const size = 5;
  const withSentinelTwo = Float32Array.from([
    2, 2, 2, 2, 2,
    2, 0.1, 0.2, 0.3, 2,
    2, 0.2, 0.4, 0.6, 2,
    2, 0.3, 0.6, 0.9, 2,
    2, 2, 2, 2, 2,
  ]);
  const withSentinelNine = Float32Array.from(
    withSentinelTwo,
    (value) => value === 2 ? 9 : value,
  );

  const blurredWithTwo = normalizedThresholdBlur(withSentinelTwo, size, 1, 2);
  const blurredWithNine = normalizedThresholdBlur(withSentinelNine, size, 1, 9);
  for (let p = 0; p < withSentinelTwo.length; p += 1) {
    if (withSentinelTwo[p] === 2) {
      assert.equal(blurredWithTwo[p], 2);
      assert.equal(blurredWithNine[p], 9);
    } else {
      assert.equal(blurredWithTwo[p], blurredWithNine[p]);
    }
  }
});

function studyEnvironment(overrides = {}) {
  const environment = { ...process.env };
  delete environment.QSDF_STUDY_SIZE;
  delete environment.QSDF_STUDY_OUTPUT;
  delete environment.QSDF_STUDY_SCENE;
  return { ...environment, ...overrides };
}

function generate(output, extra = [], environment = {}) {
  const result = spawnSync(
    process.execPath,
    [generator, "--resolution", "64", "--output", output, ...extra],
    {
      cwd: websiteRoot,
      encoding: "utf8",
      env: studyEnvironment(environment),
      maxBuffer: 4 * 1024 * 1024,
    },
  );
  assert.equal(result.status, 0, result.stderr || result.stdout);
}

function generateFromEnvironment(output, scene) {
  const result = spawnSync(
    process.execPath,
    [generator],
    {
      cwd: websiteRoot,
      encoding: "utf8",
      env: studyEnvironment({
        QSDF_STUDY_SIZE: "64",
        QSDF_STUDY_OUTPUT: output,
        QSDF_STUDY_SCENE: scene,
      }),
      maxBuffer: 4 * 1024 * 1024,
    },
  );
  assert.equal(result.status, 0, result.stderr || result.stdout);
}

async function hashes(directory) {
  const names = (await readdir(directory)).sort();
  return Object.fromEntries(await Promise.all(names.map(async (name) => {
    const bytes = await readFile(join(directory, name));
    return [name, createHash("sha256").update(bytes).digest("hex")];
  })));
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function inspectPng(bytes) {
  assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
  let offset = 8;
  let width;
  let height;
  let ended = false;
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const typeStart = offset + 4;
    const dataStart = typeStart + 4;
    const dataEnd = dataStart + length;
    const type = bytes.toString("ascii", typeStart, dataStart);
    assert.ok(dataEnd + 4 <= bytes.length, `${type} chunk exceeds the PNG file`);
    const expectedCrc = bytes.readUInt32BE(dataEnd);
    assert.equal(crc32(bytes.subarray(typeStart, dataEnd)), expectedCrc, `${type} CRC mismatch`);
    if (type === "IHDR") {
      width = bytes.readUInt32BE(dataStart);
      height = bytes.readUInt32BE(dataStart + 4);
    }
    offset = dataEnd + 4;
    if (type === "IEND") {
      ended = true;
      break;
    }
  }
  assert.equal(ended, true);
  assert.equal(offset, bytes.length);
  return { width, height };
}

function assertFiniteNumbers(value) {
  if (typeof value === "number") {
    assert.ok(Number.isFinite(value));
    return;
  }
  if (Array.isArray(value)) {
    value.forEach(assertFiniteNumbers);
    return;
  }
  if (value && typeof value === "object") Object.values(value).forEach(assertFiniteNumbers);
}

test("regenerates the complete 64px study byte-for-byte", async (context) => {
  const root = await mkdtemp(join(tmpdir(), "qsdf-study-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  const first = join(root, "first");
  const second = join(root, "second");
  generate(first);
  generate(second);
  assert.deepEqual(await hashes(first), await hashes(second));

  const dimensions = {
    "method-comparison.png": [352, 208],
    "sdf-stages.png": [280, 64],
    "threshold-map-overview-card.png": [1200, 630],
    "topology-comparison.png": [208, 208],
  };
  for (const [name, expected] of Object.entries(dimensions)) {
    const actual = inspectPng(await readFile(join(first, name)));
    assert.deepEqual([actual.width, actual.height], expected, name);
  }

  const results = JSON.parse(await readFile(join(first, "results.json"), "utf8"));
  assertFiniteNumbers(results);
  assert.equal(Object.keys(results.scenes).length, 6);
  assert.equal(
    results.aggregate.nearestKey.meanPixelErrorPercent,
    results.aggregate.pixelLinear.meanPixelErrorPercent,
  );
  assert.ok(
    results.aggregate.sdfDistanceRatio.meanPixelErrorPercent
      < results.aggregate.nearestKey.meanPixelErrorPercent,
  );
  for (const method of Object.values(results.aggregate)) {
    assert.ok(Number.isFinite(method.meanTransitionAngleErrorDegrees));
    assert.ok(method.meanTransitionAngleErrorDegrees >= 0);
    assert.ok(method.meanTransitionAngleErrorDegrees <= 90);
  }
  assert.equal(
    results.aggregate.nearestKey.meanTransitionAngleErrorDegrees,
    results.aggregate.pixelLinear.meanTransitionAngleErrorDegrees,
  );
  assert.ok(results.quantization.uint8.maxAngleErrorDegrees <= 90 / (2 * 255) + 1e-6);
  assert.ok(results.quantization.uint16.maxAngleErrorDegrees <= 90 / (2 * 65535) + 1e-6);
});

test("supports generating a single named study scene", async (context) => {
  const root = await mkdtemp(join(tmpdir(), "qsdf-study-scene-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  generate(root, ["--scene", "linear"]);
  const results = JSON.parse(await readFile(join(root, "results.json"), "utf8"));
  assert.deepEqual(Object.keys(results.scenes), ["linear"]);
  assert.deepEqual((await readdir(root)).sort(), [
    "method-comparison.png",
    "results.json",
    "sdf-stages.png",
    "threshold-map-overview-card.png",
  ]);
});

test("keeps the documented environment-variable interface", async (context) => {
  const root = await mkdtemp(join(tmpdir(), "qsdf-study-env-"));
  context.after(() => rm(root, { recursive: true, force: true }));
  generateFromEnvironment(root, "linear");
  const results = JSON.parse(await readFile(join(root, "results.json"), "utf8"));
  assert.deepEqual(results.resolution, [64, 64]);
  assert.deepEqual(Object.keys(results.scenes), ["linear"]);
  assert.ok(Number.isFinite(results.aggregate.blurredCumulative.meanTransitionAngleErrorDegrees));
});
