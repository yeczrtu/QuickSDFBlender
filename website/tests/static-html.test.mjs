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
  assert.match(html, /Quick SDF Studio 操作ガイド/);
  assert.match(html, /角度別の顔影マスクを編集/);
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

  assert.match(copy, /このページでは、インストール、編集、確認、書き出しの手順を説明します/);
  assert.match(copy, /This page describes installation, editing, review, and export/);
  assert.doesNotMatch(copy, /直すだけ|違和感だけ|この4つだけ覚えて|最初の書き出しまで/);
  assert.doesNotMatch(copy, /instead of painting every shadow|only what looks wrong|Only four things to remember|Your first export/i);
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
