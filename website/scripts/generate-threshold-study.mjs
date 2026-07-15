import { mkdir, writeFile } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { deflateSync } from "node:zlib";
import { normalizedThresholdBlur } from "./threshold-study-math.mjs";

function parseArguments(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (!argument.startsWith("--")) throw new Error(`Unknown argument: ${argument}`);
    const name = argument.slice(2);
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for --${name}`);
    if (!['resolution', 'output', 'scene'].includes(name)) throw new Error(`Unknown option: --${name}`);
    options[name] = value;
    index += 1;
  }
  return options;
}

const options = parseArguments(process.argv.slice(2));
const SIZE = Number(options.resolution ?? process.env.QSDF_STUDY_SIZE ?? 512);
if (!Number.isInteger(SIZE) || SIZE < 16 || SIZE > 4096) {
  throw new Error("--resolution must be an integer between 16 and 4096");
}
const KEY_COUNT = 7;
const SAMPLE_ANGLES = [22.5, 37.5, 52.5];
const outputOption = options.output ?? process.env.QSDF_STUDY_OUTPUT;
const outputDirectory = outputOption
  ? pathToFileURL(resolve(process.cwd(), outputOption) + sep)
  : new URL("../public/research/threshold-study/", import.meta.url);

const ALL_SCENES = [
  { id: "linear", label: "一定速度の直線" },
  { id: "nonlinear-arc", label: "速度が変わる曲線" },
  { id: "concave", label: "凹形状" },
  { id: "topology", label: "成分の出現と結合" },
  { id: "image-edge", label: "画像端を横切る形状" },
  { id: "thin-branch", label: "細線と分岐" },
];
const requestedScene = options.scene ?? process.env.QSDF_STUDY_SCENE;
const SCENES = requestedScene
  ? ALL_SCENES.filter((scene) => scene.id === requestedScene)
  : ALL_SCENES;
if (!SCENES.length) throw new Error(`Unknown scene: ${requestedScene}`);

function segmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const denominator = dx * dx + dy * dy;
  const t = denominator ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / denominator)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function thresholdField(scene, x, y) {
  switch (scene) {
    case "linear":
      return (x - 0.1) / 0.8;
    case "nonlinear-arc": {
      const radius = Math.hypot(x - 0.2, (y - 0.5) / 0.72);
      const normalized = (radius - 0.1) / 0.78;
      return Math.sign(normalized) * Math.abs(normalized) ** 1.6;
    }
    case "concave": {
      const distance = Math.min(
        segmentDistance(x, y, 0.25, 0.18, 0.25, 0.78),
        segmentDistance(x, y, 0.25, 0.78, 0.75, 0.78),
        segmentDistance(x, y, 0.75, 0.78, 0.75, 0.18),
      );
      return (distance - 0.015) / 0.24;
    }
    case "topology":
      return Math.min(
        0.05 + Math.hypot(x - 0.27, y - 0.5) / 0.53,
        0.48 + Math.hypot(x - 0.73, y - 0.5) / 0.34,
      );
    case "image-edge":
      return (Math.hypot(x + 0.1, y - 0.5) - 0.16) / 0.78;
    case "thin-branch":
      return Math.min(
        0.12 + segmentDistance(x, y, 0.15, 0.52, 0.85, 0.52) / 0.16,
        0.58 + segmentDistance(x, y, 0.5, 0.2, 0.5, 0.82) / 0.1,
      );
    default:
      throw new Error(`Unknown scene: ${scene}`);
  }
}

function proceduralMask(scene, t) {
  const mask = new Uint8Array(SIZE * SIZE);
  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) {
      const nx = (x + 0.5) / SIZE;
      const ny = (y + 0.5) / SIZE;
      mask[y * SIZE + x] = t >= thresholdField(scene, nx, ny) ? 1 : 0;
    }
  }
  return mask;
}

function edt1d(f, n) {
  const d = new Float64Array(n);
  d.fill(Infinity);
  const v = new Int32Array(n);
  const z = new Float64Array(n + 1);
  let first = -1;
  for (let q = 0; q < n; q += 1) {
    if (Number.isFinite(f[q])) {
      first = q;
      break;
    }
  }
  if (first < 0) return d;
  let k = 0;
  v[0] = first;
  z[0] = -Infinity;
  z[1] = Infinity;

  for (let q = first + 1; q < n; q += 1) {
    if (!Number.isFinite(f[q])) continue;
    let s;
    do {
      const vk = v[k];
      s = ((f[q] + q * q) - (f[vk] + vk * vk)) / (2 * q - 2 * vk);
      if (s <= z[k]) k -= 1;
    } while (s <= z[k]);
    k += 1;
    v[k] = q;
    z[k] = s;
    z[k + 1] = Infinity;
  }

  k = 0;
  for (let q = 0; q < n; q += 1) {
    while (z[k + 1] < q) k += 1;
    const delta = q - v[k];
    d[q] = delta * delta + f[v[k]];
  }
  return d;
}

function exactSquaredDistance(featureMask) {
  const columnPass = new Float64Array(SIZE * SIZE);
  const output = new Float64Array(SIZE * SIZE);
  const line = new Float64Array(SIZE);

  for (let x = 0; x < SIZE; x += 1) {
    for (let y = 0; y < SIZE; y += 1) line[y] = featureMask[y * SIZE + x] ? 0 : Infinity;
    const distances = edt1d(line, SIZE);
    for (let y = 0; y < SIZE; y += 1) columnPass[y * SIZE + x] = distances[y];
  }

  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) line[x] = columnPass[y * SIZE + x];
    const distances = edt1d(line, SIZE);
    for (let x = 0; x < SIZE; x += 1) output[y * SIZE + x] = distances[x];
  }
  return output;
}

function signedDistance(mask) {
  const light = mask;
  const shadow = Uint8Array.from(mask, (value) => value ? 0 : 1);
  const toLight = exactSquaredDistance(light);
  const toShadow = exactSquaredDistance(shadow);
  const output = new Float32Array(mask.length);
  for (let i = 0; i < mask.length; i += 1) {
    output[i] = mask[i] ? Math.sqrt(toShadow[i]) : -Math.sqrt(toLight[i]);
  }
  return output;
}

function thresholdFromKeys(keyMasks, signedDistances) {
  const threshold = new Float32Array(SIZE * SIZE);
  threshold.fill(2);
  for (let p = 0; p < threshold.length; p += 1) {
    if (keyMasks[0][p]) {
      threshold[p] = 0;
      continue;
    }
    for (let key = 0; key < KEY_COUNT - 1; key += 1) {
      if (!keyMasks[key][p] && keyMasks[key + 1][p]) {
        const a = signedDistances[key][p];
        const b = signedDistances[key + 1][p];
        const rawCrossing = Number.isFinite(a) && Number.isFinite(b) ? -a / (b - a) : 0.5;
        const crossing = Math.max(0, Math.min(1, rawCrossing));
        threshold[p] = (key + crossing) / (KEY_COUNT - 1);
        break;
      }
    }
  }
  return threshold;
}

function firstKeyThreshold(keyMasks) {
  const threshold = new Float32Array(SIZE * SIZE);
  threshold.fill(2);
  for (let p = 0; p < threshold.length; p += 1) {
    for (let key = 0; key < KEY_COUNT; key += 1) {
      if (keyMasks[key][p]) {
        threshold[p] = key / (KEY_COUNT - 1);
        break;
      }
    }
  }
  return threshold;
}

function midpointThresholdFromFirstKey(firstThreshold) {
  const halfKeyInterval = 0.5 / (KEY_COUNT - 1);
  return Float32Array.from(firstThreshold, (value) => {
    if (value < 0 || value > 1) return value;
    return Math.max(0, value - halfKeyInterval);
  });
}

function maskAtThreshold(threshold, t) {
  return Uint8Array.from(threshold, (value) => t >= value ? 1 : 0);
}

function nearestMask(keyMasks, t) {
  return keyMasks[Math.round(t * (KEY_COUNT - 1))];
}

function pixelLinearMask(keyMasks, t) {
  const position = t * (KEY_COUNT - 1);
  const left = Math.min(KEY_COUNT - 2, Math.floor(position));
  const alpha = position - left;
  const a = keyMasks[left];
  const b = keyMasks[left + 1];
  return Uint8Array.from(a, (value, p) => ((1 - alpha) * value + alpha * b[p]) >= 0.5 ? 1 : 0);
}

function mismatchRate(actual, expected) {
  let different = 0;
  for (let i = 0; i < actual.length; i += 1) if (actual[i] !== expected[i]) different += 1;
  return different / actual.length;
}

function iou(actual, expected) {
  let intersection = 0;
  let union = 0;
  for (let i = 0; i < actual.length; i += 1) {
    if (actual[i] || expected[i]) union += 1;
    if (actual[i] && expected[i]) intersection += 1;
  }
  return union ? intersection / union : 1;
}

function temporalChangeSeries(method) {
  const changes = [];
  let previous = method(0);
  for (let angle = 1; angle <= 90; angle += 1) {
    const current = method(angle / 90);
    changes.push(mismatchRate(current, previous));
    previous = current;
  }
  const mean = changes.reduce((sum, value) => sum + value, 0) / changes.length;
  const variance = changes.reduce((sum, value) => sum + (value - mean) ** 2, 0) / changes.length;
  return { mean, standardDeviation: Math.sqrt(variance), peak: Math.max(...changes) };
}

function transitionAngleError(methodThreshold, scene) {
  let errorSum = 0;
  let transitionPixelCount = 0;
  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) {
      const p = y * SIZE + x;
      const nx = (x + 0.5) / SIZE;
      const ny = (y + 0.5) / SIZE;
      const expected = thresholdField(scene, nx, ny);
      if (expected < 0 || expected > 1) continue;
      const actual = methodThreshold[p];
      const boundedActual = Math.max(0, Math.min(1, actual));
      errorSum += Math.abs(boundedActual - expected) * 90;
      transitionPixelCount += 1;
    }
  }
  return transitionPixelCount ? errorSum / transitionPixelCount : 0;
}

function evaluateMethod(method, methodThreshold, scene) {
  const mismatch = [];
  const overlaps = [];
  for (let angle = 1; angle < 90; angle += 1) {
    const t = angle / 90;
    const actual = method(t);
    const expected = proceduralMask(scene, t);
    mismatch.push(mismatchRate(actual, expected));
    overlaps.push(iou(actual, expected));
  }
  const temporal = temporalChangeSeries(method);
  return {
    meanPixelErrorPercent: average(mismatch) * 100,
    worstPixelErrorPercent: Math.max(...mismatch) * 100,
    meanIoU: average(overlaps),
    temporalChangeStdDevPercent: temporal.standardDeviation * 100,
    peakOneDegreeChangePercent: temporal.peak * 100,
    meanTransitionAngleErrorDegrees: transitionAngleError(methodThreshold, scene),
  };
}

function average(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function quantizationStats(threshold, levels) {
  const errors = [];
  for (const value of threshold) {
    if (value < 0 || value > 1) continue;
    const decoded = Math.round(value * levels) / levels;
    errors.push(Math.abs(decoded - value) * 90);
  }
  return {
    meanAngleErrorDegrees: average(errors),
    maxAngleErrorDegrees: errors.reduce((maximum, value) => Math.max(maximum, value), 0),
  };
}

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])));
  return Buffer.concat([length, typeBuffer, data, checksum]);
}

function encodePng(width, height, rgba) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  const scanlines = Buffer.alloc(height * (1 + width * 4));
  for (let y = 0; y < height; y += 1) {
    const target = y * (1 + width * 4);
    scanlines[target] = 0;
    rgba.copy(scanlines, target + 1, y * width * 4, (y + 1) * width * 4);
  }
  return Buffer.concat([
    signature,
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", deflateSync(scanlines, { level: 9 })),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

function maskPanel(mask, tint = [244, 247, 250]) {
  const pixels = Buffer.alloc(SIZE * SIZE * 4);
  for (let p = 0; p < mask.length; p += 1) {
    const offset = p * 4;
    const value = mask[p] ? tint : [15, 19, 24];
    pixels[offset] = value[0];
    pixels[offset + 1] = value[1];
    pixels[offset + 2] = value[2];
    pixels[offset + 3] = 255;
  }
  return pixels;
}

function valuePanel(values, min, max) {
  const pixels = Buffer.alloc(SIZE * SIZE * 4);
  for (let p = 0; p < values.length; p += 1) {
    const normalized = Math.max(0, Math.min(1, (values[p] - min) / (max - min)));
    const offset = p * 4;
    pixels[offset] = Math.round(26 + normalized * 218);
    pixels[offset + 1] = Math.round(70 + Math.abs(normalized - 0.5) * 120);
    pixels[offset + 2] = Math.round(244 - normalized * 190);
    pixels[offset + 3] = 255;
  }
  return pixels;
}

function composePanels(panels, columns, gap = 8) {
  const rows = Math.ceil(panels.length / columns);
  const width = columns * SIZE + (columns - 1) * gap;
  const height = rows * SIZE + (rows - 1) * gap;
  const output = Buffer.alloc(width * height * 4, 14);
  for (let p = 3; p < output.length; p += 4) output[p] = 255;
  panels.forEach((panel, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const ox = column * (SIZE + gap);
    const oy = row * (SIZE + gap);
    for (let y = 0; y < SIZE; y += 1) {
      const sourceStart = y * SIZE * 4;
      const targetStart = ((oy + y) * width + ox) * 4;
      panel.copy(output, targetStart, sourceStart, sourceStart + SIZE * 4);
    }
  });
  return { width, height, pixels: output };
}

function blitNearest(target, targetWidth, targetHeight, source, sourceWidth, sourceHeight, x, y, width, height) {
  for (let dy = 0; dy < height; dy += 1) {
    const ty = y + dy;
    if (ty < 0 || ty >= targetHeight) continue;
    const sy = Math.min(sourceHeight - 1, Math.floor(dy * sourceHeight / height));
    for (let dx = 0; dx < width; dx += 1) {
      const tx = x + dx;
      if (tx < 0 || tx >= targetWidth) continue;
      const sx = Math.min(sourceWidth - 1, Math.floor(dx * sourceWidth / width));
      const sourceOffset = (sy * sourceWidth + sx) * 4;
      const targetOffset = (ty * targetWidth + tx) * 4;
      target[targetOffset] = source[sourceOffset];
      target[targetOffset + 1] = source[sourceOffset + 1];
      target[targetOffset + 2] = source[sourceOffset + 2];
      target[targetOffset + 3] = source[sourceOffset + 3];
    }
  }
}

function fillRectangle(target, targetWidth, targetHeight, x, y, width, height, color) {
  const left = Math.max(0, x);
  const top = Math.max(0, y);
  const right = Math.min(targetWidth, x + width);
  const bottom = Math.min(targetHeight, y + height);
  for (let py = top; py < bottom; py += 1) {
    for (let px = left; px < right; px += 1) {
      const offset = (py * targetWidth + px) * 4;
      target[offset] = color[0];
      target[offset + 1] = color[1];
      target[offset + 2] = color[2];
      target[offset + 3] = 255;
    }
  }
}

function drawRightArrow(target, targetWidth, targetHeight, centerX, centerY, color) {
  fillRectangle(target, targetWidth, targetHeight, centerX - 8, centerY - 2, 11, 5, color);
  for (let step = 0; step < 7; step += 1) {
    const halfHeight = 6 - step;
    fillRectangle(
      target,
      targetWidth,
      targetHeight,
      centerX + 3 + step,
      centerY - halfHeight,
      1,
      halfHeight * 2 + 1,
      color,
    );
  }
}

function buildOverviewCard(study) {
  const width = 1200;
  const height = 630;
  const pixels = Buffer.alloc(width * height * 4);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    pixels[offset] = 12;
    pixels[offset + 1] = 17;
    pixels[offset + 2] = 23;
    pixels[offset + 3] = 255;
  }

  const stagePanels = [
    maskPanel(study.keyMasks[1]),
    maskPanel(study.keyMasks[4]),
    valuePanel(study.sdfThreshold, 0, 1),
    maskPanel(maskAtThreshold(study.sdfThreshold, 37.5 / 90), [135, 220, 176]),
  ];
  const stageColors = [
    [244, 162, 76],
    [244, 162, 76],
    [87, 169, 255],
    [135, 220, 176],
  ];
  stagePanels.forEach((panel, index) => {
    const x = 30 + index * 290;
    blitNearest(pixels, width, height, panel, SIZE, SIZE, x, 30, 270, 270);
    fillRectangle(pixels, width, height, x, 30, 270, 6, stageColors[index]);
    if (index < stagePanels.length - 1) {
      drawRightArrow(pixels, width, height, x + 280, 165, [182, 193, 205]);
    }
  });
  study.keyMasks.forEach((mask, index) => {
    const x = 30 + index * 165;
    blitNearest(pixels, width, height, maskPanel(mask), SIZE, SIZE, x, 420, 150, 150);
    fillRectangle(pixels, width, height, x, 420, 150, 4, [244, 162, 76]);
  });
  return { width, height, pixels };
}

await mkdir(outputDirectory, { recursive: true });

function buildStudy(scene) {
  const keyMasks = Array.from({ length: KEY_COUNT }, (_, key) => proceduralMask(scene, key / (KEY_COUNT - 1)));
  const signedDistances = keyMasks.map(signedDistance);
  const sdfThreshold = thresholdFromKeys(keyMasks, signedDistances);
  const firstThreshold = firstKeyThreshold(keyMasks);
  const midpointThreshold = midpointThresholdFromFirstKey(firstThreshold);
  const blurredThreshold = normalizedThresholdBlur(firstThreshold, SIZE);
  const methods = {
    nearestKey: (t) => nearestMask(keyMasks, t),
    pixelLinear: (t) => pixelLinearMask(keyMasks, t),
    blurredCumulative: (t) => maskAtThreshold(blurredThreshold, t),
    sdfDistanceRatio: (t) => maskAtThreshold(sdfThreshold, t),
  };
  const methodThresholds = {
    nearestKey: midpointThreshold,
    pixelLinear: midpointThreshold,
    blurredCumulative: blurredThreshold,
    sdfDistanceRatio: sdfThreshold,
  };
  return { scene, keyMasks, signedDistances, sdfThreshold, methods, methodThresholds };
}

const studies = new Map();
for (const scene of SCENES) {
  console.error(`Generating ${scene.id}...`);
  studies.set(scene.id, buildStudy(scene.id));
}

const sceneResults = Object.fromEntries(SCENES.map((scene) => {
  const study = studies.get(scene.id);
  return [scene.id, {
    label: scene.label,
    methods: Object.fromEntries(
      Object.entries(study.methods).map(([name, method]) => [
        name,
        evaluateMethod(method, study.methodThresholds[name], scene.id),
      ]),
    ),
  }];
}));

const methodNames = Object.keys(studies.get(SCENES[0].id).methods);
const aggregate = Object.fromEntries(methodNames.map((methodName) => {
  const values = SCENES.map((scene) => sceneResults[scene.id].methods[methodName]);
  return [methodName, {
    meanPixelErrorPercent: average(values.map((value) => value.meanPixelErrorPercent)),
    worstPixelErrorPercent: values.reduce((maximum, value) => Math.max(maximum, value.worstPixelErrorPercent), 0),
    meanIoU: average(values.map((value) => value.meanIoU)),
    temporalChangeStdDevPercent: average(values.map((value) => value.temporalChangeStdDevPercent)),
    peakOneDegreeChangePercent: values.reduce((maximum, value) => Math.max(maximum, value.peakOneDegreeChangePercent), 0),
    meanTransitionAngleErrorDegrees: average(values.map((value) => value.meanTransitionAngleErrorDegrees)),
  }];
}));

const allThresholds = new Float32Array(SCENES.length * SIZE * SIZE);
SCENES.forEach((scene, index) => allThresholds.set(studies.get(scene.id).sdfThreshold, index * SIZE * SIZE));

const results = {
  generatedAt: "2026-07-15",
  implementation: "deterministic JavaScript reference study",
  resolution: [SIZE, SIZE],
  keyAnglesDegrees: Array.from({ length: KEY_COUNT }, (_, key) => key * 90 / (KEY_COUNT - 1)),
  evaluationAnglesDegrees: "1..89 in one-degree increments",
  scenes: sceneResults,
  aggregate,
  quantization: {
    uint8: quantizationStats(allThresholds, 255),
    uint16: quantizationStats(allThresholds, 65535),
  },
};

const comparisonStudy = studies.get("concave") ?? studies.values().next().value;
const comparisonScene = comparisonStudy.scene;
const comparisonPanels = [];
for (const angle of SAMPLE_ANGLES) {
  const t = angle / 90;
  comparisonPanels.push(maskPanel(proceduralMask(comparisonScene, t), [244, 247, 250]));
  comparisonPanels.push(maskPanel(comparisonStudy.methods.nearestKey(t), [244, 162, 76]));
  comparisonPanels.push(maskPanel(comparisonStudy.methods.pixelLinear(t), [244, 162, 76]));
  comparisonPanels.push(maskPanel(comparisonStudy.methods.blurredCumulative(t), [87, 169, 255]));
  comparisonPanels.push(maskPanel(comparisonStudy.methods.sdfDistanceRatio(t), [135, 220, 176]));
}
const comparison = composePanels(comparisonPanels, 5);
await writeFile(new URL("method-comparison.png", outputDirectory), encodePng(comparison.width, comparison.height, comparison.pixels));

const topologyStudy = studies.get("topology");
if (topologyStudy) {
  const topologyPanels = [];
  for (const angle of SAMPLE_ANGLES) {
    const t = angle / 90;
    topologyPanels.push(maskPanel(proceduralMask("topology", t), [244, 247, 250]));
    topologyPanels.push(maskPanel(topologyStudy.methods.blurredCumulative(t), [87, 169, 255]));
    topologyPanels.push(maskPanel(topologyStudy.methods.sdfDistanceRatio(t), [135, 220, 176]));
  }
  const topology = composePanels(topologyPanels, 3);
  await writeFile(new URL("topology-comparison.png", outputDirectory), encodePng(topology.width, topology.height, topology.pixels));
}

const stagePanels = [
  maskPanel(comparisonStudy.keyMasks[2]),
  valuePanel(comparisonStudy.signedDistances[2], -80, 80),
  maskPanel(comparisonStudy.keyMasks[3]),
  valuePanel(comparisonStudy.sdfThreshold, 0, 1),
];
const stages = composePanels(stagePanels, 4);
await writeFile(new URL("sdf-stages.png", outputDirectory), encodePng(stages.width, stages.height, stages.pixels));
const overviewCard = buildOverviewCard(comparisonStudy);
await writeFile(new URL("threshold-map-overview-card.png", outputDirectory), encodePng(overviewCard.width, overviewCard.height, overviewCard.pixels));
await writeFile(new URL("results.json", outputDirectory), `${JSON.stringify(results, null, 2)}\n`, "utf8");

console.log(JSON.stringify(results, null, 2));
