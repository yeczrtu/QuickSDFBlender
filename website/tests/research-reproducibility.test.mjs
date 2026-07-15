import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const websiteRoot = resolve(import.meta.dirname, "..");
const generator = resolve(websiteRoot, "scripts/generate-threshold-study.mjs");

function generate(output, extra = []) {
  const result = spawnSync(
    process.execPath,
    [generator, "--resolution", "64", "--output", output, ...extra],
    { cwd: websiteRoot, encoding: "utf8", maxBuffer: 4 * 1024 * 1024 },
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
