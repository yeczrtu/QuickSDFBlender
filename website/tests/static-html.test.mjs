import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

const basePath = "/QuickSDFBlender";
const mediaFiles = [
  "quick-sdf-advanced.png",
  "quick-sdf-angle-seek-poster.png",
  "quick-sdf-angle-seek.gif",
  "quick-sdf-create-and-edit.png",
  "quick-sdf-export.png",
  "quick-sdf-normal-guide-and-paint.png",
  "quick-sdf-single-playhead.png",
  "quick-sdf-studio-overview.png",
  "quick-sdf-threshold-example.png",
];

function rasterDimensions(bytes) {
  if (bytes.subarray(1, 4).toString("ascii") === "PNG") {
    return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)];
  }
  const signature = bytes.subarray(0, 6).toString("ascii");
  if (signature === "GIF87a" || signature === "GIF89a") {
    return [bytes.readUInt16LE(6), bytes.readUInt16LE(8)];
  }
  throw new Error("Unsupported raster format");
}

function metaContent(html, attribute, key) {
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = html.match(new RegExp(`<meta ${attribute}="${escapedKey}" content="([^"]*)"`));
  assert.ok(match, `${attribute}=${key} must be present`);
  return match[1];
}

function articleJsonLd(html) {
  const match = html.match(/<script type="application\/ld\+json">([^<]+)<\/script>/);
  assert.ok(match, "article JSON-LD must be present");
  return JSON.parse(match[1]);
}

function assertFiniteNumbers(value, path = "results") {
  if (typeof value === "number") {
    assert.ok(Number.isFinite(value), `${path} must be finite`);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => assertFiniteNumbers(entry, `${path}[${index}]`));
    return;
  }
  if (value && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) assertFiniteNumbers(entry, `${path}.${key}`);
  }
}

test("exports a GitHub Pages document with project-relative assets", async () => {
  const html = await readFile(new URL("../out/index.html", import.meta.url), "utf8");

  assert.match(html, /<html[^>]+lang="ja"/i);
  assert.match(html, /Quick SDF Paint/);
  assert.match(html, /v0\.7\.1/);
  assert.match(html, /トゥーンレンダリング用の顔影スレッショルドマップを作成/);
  assert.match(html, /角度別の白黒マスク/);
  assert.match(html, /SDF距離補間/);
  assert.match(html, /16-bit RGBAスレッショルドマップ/);
  assert.match(html, /Export Threshold Map/);
  assert.match(html, /href="https:\/\/yeczrtu\.github\.io\/QuickSDFBlender\/"/);
  assert.match(html, /\/QuickSDFBlender\/_next\//);
  assert.match(html, /\/QuickSDFBlender\/media\/quick-sdf-studio-overview\.png/);
  assert.doesNotMatch(html, /(?:src|href)="\/media\//);

  const localReferences = [...html.matchAll(/(?:src|href)="([^"]+)"/g)]
    .map((match) => match[1])
    .filter((reference) => reference.startsWith(`${basePath}/`));

  for (const reference of localReferences) {
    const relativePath = decodeURIComponent(
      reference.slice(basePath.length + 1).split(/[?#]/, 1)[0],
    );
    if (!relativePath) continue;
    const outputPath = relativePath.endsWith("/") ? `${relativePath}index.html` : relativePath;
    const asset = await stat(new URL(`../out/${outputPath}`, import.meta.url));
    assert.ok(asset.isFile(), `${reference} must resolve to an exported file`);
  }
});

test("keeps the guide factual in Japanese and English", async () => {
  const [page, layout] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
  ]);
  const copy = `${page}\n${layout}`;

  assert.match(copy, /このページでは、Quick SDF Paintのインストール、編集、確認、書き出しの手順を説明します/);
  assert.match(copy, /This page describes how to install Quick SDF Paint, edit and review the masks, and export the result/);
  assert.match(copy, /Create face-shadow threshold maps for toon rendering/);
  assert.match(copy, /SDFは角度別マスクの境界を補間する生成手法/);
  assert.match(copy, /describes SDF as the method used to interpolate boundaries/);
  assert.doesNotMatch(copy, /直すだけ|違和感だけ|この4つだけ覚えて|最初の書き出しまで/);
  assert.doesNotMatch(copy, /instead of painting every shadow|only what looks wrong|Only four things to remember|Your first export/i);
});

test("uses sourced terminology without presenting one spelling as a standard name", async () => {
  const [html, page, layout, packageManifest] = await Promise.all([
    readFile(new URL("../out/index.html", import.meta.url), "utf8"),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  const publicCopy = `${html}\n${page}\n${layout}`;
  const disallowedLightmapAliases = new RegExp(["face\\s+lightmap", "SDF\\s+lightmap"].join("|"), "i");
  const disallowedStandardClaims = /(?:Face\s?SDF|SDF Face Shadow)(?:\s+is|\s*(?:とは|は))[^.。\n]{0,40}(?:common|general|standard|widely used|de facto|一般名称|一般的|標準名称|標準的)/i;
  const deprecatedLabels = new RegExp([
    ["Quick", "SDF", "Studio"].join("\\s+"),
    ["Export", "Face", "Shadow", "Texture"].join("\\s+"),
  ].join("|"));

  assert.match(html, /SDF Face Shadow/);
  assert.match(html, /Shadow SDF mode/);
  assert.match(html, /FaceSDF textures/);
  assert.match(html, /Face SDF Tex/);
  assert.match(html, /SDF_FaceShadow/);
  assert.match(html, /sdf shadow mask/);
  assert.match(html, /Face Shadow Map/);
  assert.match(html, /SDF-based face shadow map/);
  assert.match(html, /face SDF shadow/);
  assert.match(html, /SDF Shadow Map/);
  assert.match(html, /SDF Shadow Texture/);
  assert.match(html, /Shadow Threshold Map/);
  assert.match(html, /Face Threshold Map/);
  assert.match(html, /github\.com\/lilxyzw\/lilToon\/blob\/master\/Assets\/lilToon\/CHANGELOG\.md/);
  assert.match(html, /potatoon\.dev\/en\/features\/material-settings/);
  assert.match(html, /github\.com\/ChiliMilk\/URP_Toon/);
  assert.match(html, /github\.com\/akasaki1211\/sdf_shadow_threshold_map/);
  assert.match(html, /erichu33\.github\.io\/ASPDocs\/en\/articles\/face-shadow-map-creation-and-baking-workflow\.html/);
  assert.match(html, /github\.com\/entropy622\/Unity-URP-Shader-For-Starrail-Characters/);
  assert.match(html, /github\.com\/natane010\/natane_toon_shader/);
  assert.match(html, /cgworld\.jp\/article\/202306-hifirush01\.html/);
  assert.match(page, /SDF Face Shadow[^}\n]+github\.com\/lilxyzw\/lilToon/);
  assert.match(page, /FaceSDF textures[^}\n]+potatoon\.dev\/en\/features\/material-settings/);
  assert.match(page, /SDF_FaceShadow[^}\n]+github\.com\/ChiliMilk\/URP_Toon/);
  assert.match(page, /SDF-based face shadow map[^}\n]+erichu33\.github\.io\/ASPDocs/);
  assert.match(page, /face SDF shadow[^}\n]+github\.com\/entropy622\/Unity-URP-Shader-For-Starrail-Characters/);
  assert.match(page, /SDF Shadow Map[^}\n]+github\.com\/natane010\/natane_toon_shader/);
  assert.match(page, /Shadow Threshold Map[^}\n]+github\.com\/akasaki1211\/sdf_shadow_threshold_map/);
  assert.match(page, /Face Threshold Map[^}\n]+cgworld\.jp\/article\/202306-hifirush01\.html/);
  assert.match(page, /Face Shadow Map[^}\n]+erichu33\.github\.io\/ASPDocs/);
  assert.doesNotMatch(publicCopy, disallowedStandardClaims);
  assert.doesNotMatch(publicCopy, disallowedLightmapAliases);
  assert.doesNotMatch(publicCopy, deprecatedLabels);
  assert.match(packageManifest, /"name": "quick-sdf-paint-docs"/);
  assert.match(packageManifest, /"version": "0\.7\.1"/);
});

test("keeps terminology as a lower-page reference", async () => {
  const html = await readFile(new URL("../out/index.html", import.meta.url), "utf8");
  const workflow = html.indexOf('id="workflow"');
  const help = html.indexOf('id="help"');
  const terminology = html.indexOf('id="terminology-title"');
  const reference = html.indexOf("reference-section");

  assert.ok(workflow >= 0 && help > workflow);
  assert.ok(terminology > help);
  assert.ok(reference > terminology);
});

test("keeps lilToon and liltoonUE out of the product definition", async () => {
  const [page, layout] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
  ]);
  const japaneseCopy = page.slice(page.indexOf("ja: {"), page.indexOf("en: {"));
  const englishCopy = page.slice(page.indexOf("en: {"), page.indexOf("} as const"));
  const japaneseDefinition = japaneseCopy.slice(japaneseCopy.indexOf("guideTitle:"), japaneseCopy.indexOf("pipelineLabel:"));
  const englishDefinition = englishCopy.slice(englishCopy.indexOf("guideTitle:"), englishCopy.indexOf("pipelineLabel:"));

  assert.equal(/lilToon|liltoonUE/.test(layout), false);
  assert.equal(/lilToon|liltoonUE/.test(japaneseDefinition), false);
  assert.equal(/lilToon|liltoonUE/.test(englishDefinition), false);
  assert.ok(japaneseCopy.indexOf("liltoonUE") > japaneseCopy.indexOf("outputBody:"));
  assert.ok(englishCopy.indexOf("liltoonUE") > englishCopy.indexOf("outputBody:"));
});

test("shows the Kipfel credit immediately after its first character image", async () => {
  const [html, page] = await Promise.all([
    readFile(new URL("../out/index.html", import.meta.url), "utf8"),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(html, /id="model-credit-title"/);
  assert.match(html, /オリジナル3Dモデル「キプフェル \(Kipfel\)」/);
  assert.match(html, /モデル制作：かめ山[^<]*©もち山金魚/);
  assert.match(html, /https:\/\/mukumi\.booth\.pm\/items\/5813187/);
  assert.match(html, /https:\/\/mochiyama\.com\/kipfel_manual_jp/);
  assert.match(html, /公式・公認プロジェクトではありません/);
  assert.ok(
    page.indexOf("quick-sdf-create-and-edit.png") < page.indexOf('className="model-credit model-credit-inline"'),
    "the first character image must appear before its credit",
  );
  assert.ok(
    html.indexOf('id="model-credit-title"') < html.indexOf('id="step-2"'),
    "the character credit must remain attached to the first illustrated step",
  );
  assert.ok((html.match(/©もち山金魚/g) ?? []).length >= 2);

  assert.match(page, /Character used in the examples/);
  assert.match(page, /Model by かめ山 · ©もち山金魚/);
  assert.match(page, /not official or endorsed projects/);
});

test("ships the documented captures and the Pages marker", async () => {
  for (const fileName of mediaFiles) {
    const file = await stat(new URL(`../out/media/${fileName}`, import.meta.url));
    assert.ok(file.isFile(), `${fileName} must be a regular file`);
    assert.ok(file.size > 0, `${fileName} must not be empty`);
  }

  const animation = await stat(new URL("../out/media/quick-sdf-angle-seek.gif", import.meta.url));
  assert.ok(animation.size <= 2_000_000, "angle-seek animation must stay at or below 2 MB");

  const noJekyll = await stat(new URL("../out/.nojekyll", import.meta.url));
  assert.ok(noJekyll.isFile());
  assert.equal(basePath, "/QuickSDFBlender");
});

test("uses intrinsic raster dimensions and a reduced-motion timeline poster", async () => {
  const pages = [
    "../out/index.html",
    "../out/articles/face-shadow-threshold-map/index.html",
    "../out/articles/sdf-threshold-interpolation/index.html",
    "../out/articles/blender-threshold-map-workflow/index.html",
  ];

  for (const page of pages) {
    const html = await readFile(new URL(page, import.meta.url), "utf8");
    const imageTags = [...html.matchAll(/<img\b[^>]*\bsrc="([^"]+)"[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"[^>]*>/g)];
    for (const [, source, width, height] of imageTags) {
      if (!source.startsWith(`${basePath}/media/`) && !source.startsWith(`${basePath}/research/`)) continue;
      const bytes = await readFile(new URL(`../public/${source.slice(basePath.length + 1)}`, import.meta.url));
      assert.deepEqual(
        [Number(width), Number(height)],
        rasterDimensions(bytes),
        `${source} must use its intrinsic dimensions`,
      );
    }
  }

  const workflow = await readFile(
    new URL("../out/articles/blender-threshold-map-workflow/index.html", import.meta.url),
    "utf8",
  );
  assert.match(
    workflow,
    /<source media="\(prefers-reduced-motion: reduce\)" srcSet="\/QuickSDFBlender\/media\/quick-sdf-angle-seek-poster\.png"\/>/,
  );
  assert.match(workflow, /<img src="\/QuickSDFBlender\/media\/quick-sdf-angle-seek\.gif"/);
});

const articlePages = [
  {
    slug: "face-shadow-threshold-map",
    title: "顔影スレッショルドマップとは",
    uniqueText: "制作から表示までを4段階で捉える",
    evidence: "本記事の整理方法",
  },
  {
    slug: "sdf-threshold-interpolation",
    title: "SDF距離補間の比較",
    uniqueText: "比較する4つの方法",
    evidence: "比較実験の条件",
  },
  {
    slug: "blender-threshold-map-workflow",
    title: "Quick SDF Paint 0.7.1で顔影スレッショルドマップを作る",
    uniqueText: "完成までの最短5手順",
    evidence: "動作確認条件",
  },
];

test("exports an index and three distinct long-form articles", async () => {
  const index = await readFile(new URL("../out/articles/index.html", import.meta.url), "utf8");
  assert.match(index, /顔影スレッショルドマップの仕組みと制作方法/);
  assert.match(index, /基本的な仕組み、同一条件での補間比較、Blender 5\.1での実践手順/);
  assert.doesNotMatch(index, /既存資料の要約だけでなく|既存要約ではない/);

  assert.equal(metaContent(index, "property", "og:title"), metaContent(index, "name", "twitter:title"));
  assert.equal(metaContent(index, "property", "og:description"), metaContent(index, "name", "twitter:description"));
  assert.equal(metaContent(index, "property", "og:image"), metaContent(index, "name", "twitter:image"));
  assert.equal(metaContent(index, "name", "twitter:card"), "summary_large_image");

  for (const article of articlePages) {
    const html = await readFile(new URL(`../out/articles/${article.slug}/index.html`, import.meta.url), "utf8");
    assert.match(html, new RegExp(article.title));
    assert.match(html, new RegExp(article.uniqueText));
    assert.match(html, new RegExp(article.evidence));
    const jsonLd = articleJsonLd(html);
    assert.ok(Array.isArray(jsonLd["@graph"]));
    const techArticle = jsonLd["@graph"].find((entry) => entry["@type"] === "TechArticle");
    const breadcrumbs = jsonLd["@graph"].find((entry) => entry["@type"] === "BreadcrumbList");
    assert.ok(techArticle);
    assert.ok(breadcrumbs);
    assert.deepEqual(techArticle.author, {
      "@type": "Organization",
      name: "Quick SDF Paint contributors",
      url: "https://github.com/yeczrtu/QuickSDFBlender",
    });
    assert.equal(techArticle.publisher.name, "Quick SDF Paint");
    assert.equal(breadcrumbs.itemListElement.length, 3);
    assert.match(html, /<nav class="article-breadcrumb page-shell" aria-label="パンくず"><ol>/);
    assert.match(html, new RegExp(`https://yeczrtu\\.github\\.io/QuickSDFBlender/articles/${article.slug}/`));
    assert.equal(metaContent(html, "property", "og:title"), metaContent(html, "name", "twitter:title"));
    assert.equal(metaContent(html, "property", "og:description"), metaContent(html, "name", "twitter:description"));
    assert.equal(metaContent(html, "property", "og:image"), metaContent(html, "name", "twitter:image"));
    assert.equal(metaContent(html, "name", "twitter:card"), "summary_large_image");
    assert.match(html, /執筆・検証/);
    assert.match(html, /Quick SDF Paint contributors/);
    assert.match(html, /<dt>発行元<\/dt><dd><a href="\/QuickSDFBlender\/">Quick SDF Paint<\/a><\/dd>/);
    assert.match(html, /関連する解説/);
    assert.doesNotMatch(html, /この記事で独自に行ったこと/);
    assert.doesNotMatch(html, /Face\s?SDF(?:は|とは)[^<。]{0,20}(?:一般名称です|標準名称です|一般的な名称です)/i);
  }
});

test("keeps article tables and cross-links accessible and contextual", async () => {
  const articleHtml = Object.fromEntries(await Promise.all(articlePages.map(async ({ slug }) => [
    slug,
    await readFile(new URL(`../out/articles/${slug}/index.html`, import.meta.url), "utf8"),
  ])));

  for (const html of Object.values(articleHtml)) {
    for (const match of html.matchAll(/<table\b[\s\S]*?<\/table>/g)) {
      const table = match[0];
      assert.match(table, /<caption>[^<]+<\/caption>/);
      const headers = [...table.matchAll(/<th\b([^>]*)>/g)];
      assert.ok(headers.length > 0);
      for (const [, attributes] of headers) assert.match(attributes, /scope="(?:col|row)"/);
    }
    assert.doesNotMatch(html, /大形|停止画/);
  }

  assert.match(articleHtml["face-shadow-threshold-map"], /articles\/sdf-threshold-interpolation\//);
  assert.match(articleHtml["face-shadow-threshold-map"], /articles\/blender-threshold-map-workflow\//);
  assert.match(articleHtml["sdf-threshold-interpolation"], /articles\/blender-threshold-map-workflow\//);
  assert.match(articleHtml["face-shadow-threshold-map"], /物理的なライト角/);
  assert.match(articleHtml["face-shadow-threshold-map"], /そのものではありません/);
  assert.match(articleHtml["sdf-threshold-interpolation"], /物理的なライト角/);
  assert.match(articleHtml["sdf-threshold-interpolation"], /ではありません/);
  assert.match(articleHtml["blender-threshold-map-workflow"], /物理的なライト角ではありません/);
});

test("keeps Japanese article prose, notation, and UI labels consistent", async () => {
  const [index, data, layout, foundation, comparison, workflow, css] = await Promise.all([
    readFile(new URL("../app/articles/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/articles/article-data.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/articles/article-layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/articles/face-shadow-threshold-map/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/articles/sdf-threshold-interpolation/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/articles/blender-threshold-map-workflow/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
  ]);
  const articleSource = `${index}\n${data}\n${foundation}\n${comparison}\n${workflow}`;
  const proseWithoutUiLabels = articleSource.replace(/<code>[\s\S]*?<\/code>/g, "");

  for (const pattern of [
    /顔Mesh/,
    /UV island/,
    /bit depth/,
    /round-to-nearest/,
    /決定的な検証/,
    /時間方向/,
    /切替角/,
    /近距離Point Light/,
    /0～90度/,
    /0°–90°/,
    /0–1/,
    /\b(?:Mesh|Timeline|Canvas|Project|Pose|Shape Key|Point Light)\b/,
  ]) assert.doesNotMatch(proseWithoutUiLabels, pattern);

  assert.match(data, /顔影スレッショルドマップとは：仕組みと制作方法/);
  assert.match(data, /角度別マスクを1枚にまとめる方法：SDF距離補間の比較/);
  assert.match(data, /Quick SDF Paint 0\.7\.1で顔影スレッショルドマップを作る：Blenderでの実践手順/);
  assert.match(index, /Quick SDF Paint 技術解説/);
  assert.match(layout, /<dt>読了時間<\/dt>/);
  assert.match(layout, /<dt>発行元<\/dt>/);

  assert.match(foundation, /<code>L<\/code>[^<]*正規化ライト方向ベクトル/);
  assert.match(foundation, /<code>φ<\/code>[\s\S]{0,100}水平方向の実ライト角/);
  assert.match(foundation, /<code>t<\/code>[\s\S]{0,180}0～1に正規化した制作進行度/);
  assert.match(foundation, /<code>u\(p\)<\/code>[\s\S]{0,80}正規化位置/);
  assert.match(foundation, /Light\(t, p\) = \[ t ≥ u\(p\) \]/);
  assert.match(foundation, /<SourceList>[\s\S]*?<h2>主な参考資料<\/h2>/);

  assert.match(comparison, /512 × 512 px/);
  assert.match(comparison, /半径8 px、カーネル幅17 px/);
  assert.match(comparison, /切替位置MAE（制作目盛り換算、°）/);
  assert.match(comparison, /className="article-table-scroll"/);
  assert.match(comparison, /className="article-wide-table"/);
  assert.match(comparison, /<SourceList>[\s\S]*?<h2>検証データと主な参考資料<\/h2>/);

  assert.match(workflow, /1024 px、0°～90°/);
  assert.match(workflow, /<code>Create &amp; Edit<\/code>/);
  assert.match(workflow, /<code>Light<\/code>／<code>Shadow<\/code>/);
  assert.match(workflow, /<code>Material Slot<\/code>/);
  assert.match(workflow, /<code>SDF Area<\/code>/);

  assert.match(css, /\.article-hero h1[\s\S]*?text-wrap: balance/);
  assert.match(css, /\.article-body[\s\S]*?line-break: strict/);
  assert.match(css, /\.article-table-scroll[\s\S]*?overflow-x: auto/);
  assert.match(css, /@media \(max-width: 760px\)[\s\S]*?font-size: clamp\(30px, 9vw, 34px\)/);
});

test("publishes source-backed original research rather than unsupported SDF claims", async () => {
  const [html, resultsText, generator, studyMath] = await Promise.all([
    readFile(new URL("../out/articles/sdf-threshold-interpolation/index.html", import.meta.url), "utf8"),
    readFile(new URL("../public/research/threshold-study/results.json", import.meta.url), "utf8"),
    readFile(new URL("../scripts/generate-threshold-study.mjs", import.meta.url), "utf8"),
    readFile(new URL("../scripts/threshold-study-math.mjs", import.meta.url), "utf8"),
  ]);
  const results = JSON.parse(resultsText);

  assertFiniteNumbers(results);
  assert.deepEqual(results.resolution, [512, 512]);
  assert.equal(Object.keys(results.scenes).length, 6);
  assert.deepEqual(results.aggregate.nearestKey, results.aggregate.pixelLinear);
  assert.ok(
    results.aggregate.sdfDistanceRatio.meanPixelErrorPercent
      < results.aggregate.nearestKey.meanPixelErrorPercent,
  );
  assert.ok(results.quantization.uint16.maxAngleErrorDegrees < 0.001);
  assert.match(generator, /function exactSquaredDistance/);
  assert.match(generator, /function thresholdField/);
  assert.match(generator, /const ALL_SCENES/);
  assert.match(studyMath, /function normalizedThresholdBlur/);
  assert.match(studyMath, /blurredValues\[p\] \/ blurredWeights\[p\]/);

  const labels = {
    nearestKey: "最近傍キー",
    pixelLinear: "画素線形＋二値化",
    blurredCumulative: "初回Lightキー＋正規化ボックスブラー",
    sdfDistanceRatio: "SDF距離比（exact EDT）",
  };
  for (const [method, label] of Object.entries(labels)) {
    const metrics = results.aggregate[method];
    const row = [
      `<th scope="row">${label}</th>`,
      `<td>${metrics.meanPixelErrorPercent.toFixed(2)}%</td>`,
      `<td>${metrics.meanIoU.toFixed(3)}</td>`,
      `<td>${metrics.temporalChangeStdDevPercent.toFixed(2)}%</td>`,
      `<td>${metrics.peakOneDegreeChangePercent.toFixed(2)}%</td>`,
      `<td>${metrics.meanTransitionAngleErrorDegrees.toFixed(3)}°</td>`,
    ].join("");
    assert.ok(html.includes(row), `${method} metrics must match results.json`);
  }
  assert.match(html, /結果を見た後のパラメーター調整なし/);
  assert.match(html, /1°～89°の89点/);
  assert.match(html, /既定8キーとは異なり/);
  assert.doesNotMatch(html, /89中間角度|5度刻みの36枚|d_i\+1|S_i\+1|C0連続/);
  assert.match(html, /最寄りの反対クラスの画素中心までの距離/);
  assert.match(html, /元の連続輪郭[^<]*復元できるという意味ではありません/);
  assert.match(html, /website\/scripts\/generate-threshold-study\.mjs/);
  assert.match(html, /website\/public\/research\/threshold-study\/results\.json/);
  assert.match(html, /SDFなら真の形状変化を復元できる[^<]*証明ではありません/);
  assert.match(html, /theoryofcomputing\.org\/articles\/v008a019/);
  assert.match(html, /TPAMI\.2003\.1177156/);
});

test("documents the Quick SDF Paint 0.7.1 artist workflow without hidden steps", async () => {
  const html = await readFile(
    new URL("../out/articles/blender-threshold-map-workflow/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /Quick SDF Paint 0\.7\.1を使い/);
  assert.match(html, /完成までの最短5手順/);
  assert.match(html, /16-bit RGBA PNG/);
  assert.match(html, /0°は<code>Light Starts<\/code>、90°は<code>Full Light<\/code>/);
  assert.match(html, /Shadow Amount[\s\S]{0,80}調整し、その後に<code>Update Shadow Guide<\/code>/);
  assert.match(html, /キーの間で実際に画素を変更した場合に限り[^<]*自動キー/);
  assert.match(html, /スクラブして確認するだけでは、画像もキーも増えません/);
  assert.match(html, /<code>Undo<\/code>[\s\S]{0,80}<code>Delete<\/code>/);
  assert.match(html, /SDF Area[\s\S]{0,100}Quick SDF Paint共通の補助マスク/);
  assert.match(html, /同じUV座標へ重なっている場合[^<]*補助マスクだけでは分離できません[\s\S]{0,120}マテリアルスロットまたはUVを分ける必要/);
});

test("limits production claims to what the cited public sources support", async () => {
  const html = await readFile(
    new URL("../out/articles/face-shadow-threshold-map/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /ライトを等間隔に回し、複数の二値画像を生成・修正/);
  assert.match(html, /Anime Shading Plusの公開手順[^<]*9枚/);
  assert.doesNotMatch(html, /5度刻み|36枚/);
  assert.match(html, /本記事の整理方法/);
});

test("ships the reproducible study assets and raw measurements", async () => {
  for (const fileName of [
    "method-comparison.png",
    "sdf-stages.png",
    "threshold-map-overview-card.png",
    "topology-comparison.png",
    "results.json",
  ]) {
    const file = await stat(new URL(`../out/research/threshold-study/${fileName}`, import.meta.url));
    assert.ok(file.isFile());
    assert.ok(file.size > 0);
  }
});

test("places the Kipfel credit immediately after the first character image in the practical article", async () => {
  const html = await readFile(new URL("../out/articles/blender-threshold-map-workflow/index.html", import.meta.url), "utf8");
  const firstCharacterImage = html.indexOf("quick-sdf-create-and-edit.png");
  const credit = html.indexOf("article-model-credit-title");
  const nextCharacterImage = html.indexOf("quick-sdf-studio-overview.png", credit);
  assert.ok(firstCharacterImage >= 0 && credit > firstCharacterImage);
  assert.ok(nextCharacterImage > credit);
  assert.match(html, /オリジナル3Dモデル「キプフェル（Kipfel）」/);
  assert.match(html, /©もち山金魚/);
  assert.match(html, /公式・公認プロジェクトではありません/);
});

test("exports crawlable sitemap and robots metadata", async () => {
  const [sitemap, robots] = await Promise.all([
    readFile(new URL("../out/sitemap.xml", import.meta.url), "utf8"),
    readFile(new URL("../out/robots.txt", import.meta.url), "utf8"),
  ]);
  assert.match(sitemap, /https:\/\/yeczrtu\.github\.io\/QuickSDFBlender\/articles\//);
  for (const article of articlePages) assert.match(sitemap, new RegExp(article.slug));
  assert.match(robots, /Sitemap: https:\/\/yeczrtu\.github\.io\/QuickSDFBlender\/sitemap\.xml/);
});

test("exports the favicon and a noindex not-found page", async () => {
  const [home, notFound] = await Promise.all([
    readFile(new URL("../out/index.html", import.meta.url), "utf8"),
    readFile(new URL("../out/404.html", import.meta.url), "utf8"),
  ]);
  assert.match(home, /rel="icon"[^>]*\/QuickSDFBlender\/favicon\.svg/);
  assert.match(notFound, /name="robots" content="noindex, nofollow"/);
  assert.doesNotMatch(notFound, /rel="canonical"/);
});
