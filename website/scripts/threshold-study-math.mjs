export function boxBlur(values, size, radius = 8) {
  if (values.length !== size * size) {
    throw new Error("boxBlur expects a square value field");
  }

  const temporary = new Float32Array(values.length);
  const output = new Float32Array(values.length);
  for (let y = 0; y < size; y += 1) {
    let sum = 0;
    for (let x = -radius; x <= radius; x += 1) {
      const sx = Math.max(0, Math.min(size - 1, x));
      sum += values[y * size + sx];
    }
    for (let x = 0; x < size; x += 1) {
      temporary[y * size + x] = sum / (radius * 2 + 1);
      const remove = Math.max(0, x - radius);
      const add = Math.min(size - 1, x + radius + 1);
      sum += values[y * size + add] - values[y * size + remove];
    }
  }
  for (let x = 0; x < size; x += 1) {
    let sum = 0;
    for (let y = -radius; y <= radius; y += 1) {
      const sy = Math.max(0, Math.min(size - 1, y));
      sum += temporary[sy * size + x];
    }
    for (let y = 0; y < size; y += 1) {
      output[y * size + x] = sum / (radius * 2 + 1);
      const remove = Math.max(0, y - radius);
      const add = Math.min(size - 1, y + radius + 1);
      sum += temporary[add * size + x] - temporary[remove * size + x];
    }
  }
  return output;
}

export function normalizedThresholdBlur(values, size, radius = 8, alwaysShadowSentinel = 2) {
  const weightedValues = new Float32Array(values.length);
  const validWeights = new Float32Array(values.length);
  const alwaysShadow = new Uint8Array(values.length);

  for (let p = 0; p < values.length; p += 1) {
    const valid = values[p] >= 0 && values[p] <= 1;
    if (valid) {
      weightedValues[p] = values[p];
      validWeights[p] = 1;
    } else {
      alwaysShadow[p] = 1;
    }
  }

  const blurredValues = boxBlur(weightedValues, size, radius);
  const blurredWeights = boxBlur(validWeights, size, radius);
  const output = new Float32Array(values.length);
  output.fill(alwaysShadowSentinel);
  for (let p = 0; p < values.length; p += 1) {
    if (alwaysShadow[p]) continue;
    output[p] = blurredWeights[p] > 0
      ? Math.max(0, Math.min(1, blurredValues[p] / blurredWeights[p]))
      : values[p];
  }
  return output;
}
