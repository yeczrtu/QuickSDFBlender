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
    const asset = await stat(new URL(`../out/${relativePath}`, import.meta.url));
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
