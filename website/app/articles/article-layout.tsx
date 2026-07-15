import type { ReactNode } from "react";
import {
  absoluteArticleUrl,
  articlePath,
  articles,
  basePath,
  siteRoot,
  type ArticleRecord,
  type ArticleSlug,
} from "./article-data";

export type TocItem = { id: string; label: string };

export function ArticleLayout({
  article,
  lead,
  toc,
  children,
}: {
  article: ArticleRecord;
  lead: string;
  toc: readonly TocItem[];
  children: ReactNode;
}) {
  const articleJsonLd = {
    "@type": "TechArticle",
    headline: article.title,
    description: article.description,
    datePublished: article.published,
    dateModified: article.modified,
    inLanguage: "ja-JP",
    mainEntityOfPage: absoluteArticleUrl(article.slug),
    image: article.image.startsWith("http")
      ? article.image
      : `https://yeczrtu.github.io${article.image}`,
    author: {
      "@type": "Organization",
      name: "Quick SDF Paint contributors",
      url: "https://github.com/yeczrtu/QuickSDFBlender",
    },
    publisher: {
      "@type": "Organization",
      name: "Quick SDF Paint",
      url: siteRoot,
    },
  };
  const jsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      articleJsonLd,
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: "Quick SDF Paint", item: siteRoot },
          { "@type": "ListItem", position: 2, name: "解説記事", item: `${siteRoot}articles/` },
          { "@type": "ListItem", position: 3, name: article.shortTitle, item: absoluteArticleUrl(article.slug) },
        ],
      },
    ],
  };

  return (
    <>
      <a className="skip-link" href="#article-body">本文へ移動</a>
      <header className="article-site-header">
        <a className="brand" href={`${basePath}/`} aria-label="Quick SDF Paint 操作ガイド">
          <span className="brand-mark" aria-hidden="true" />
          <span>Quick SDF Paint</span>
          <small>解説記事</small>
        </a>
        <nav aria-label="記事ナビゲーション">
          <a href={`${basePath}/articles/`}>記事一覧</a>
          <a href={`${basePath}/`}>操作ガイド</a>
          <a href="https://github.com/yeczrtu/QuickSDFBlender">GitHub</a>
        </nav>
      </header>

      <main>
        <nav className="article-breadcrumb page-shell" aria-label="パンくず">
          <ol>
            <li><a href={`${basePath}/`}>Quick SDF Paint</a></li>
            <li><a href={`${basePath}/articles/`}>解説記事</a></li>
            <li><span aria-current="page">{article.shortTitle}</span></li>
          </ol>
        </nav>

        <header className="article-hero page-shell">
          <p className="article-category">{article.category}</p>
          <h1>{article.title}</h1>
          <p className="article-lead">{lead}</p>
          <dl className="article-meta">
            <div><dt>対象</dt><dd>{article.audience}</dd></div>
            <div><dt>読了目安</dt><dd>{article.readingTime}</dd></div>
            <div><dt>公開日</dt><dd><time dateTime={article.published}>{formatArticleDate(article.published)}</time></dd></div>
            <div>
              <dt>執筆・検証</dt>
              <dd><a href="https://github.com/yeczrtu/QuickSDFBlender">Quick SDF Paint contributors</a></dd>
            </div>
            <div><dt>公開</dt><dd><a href={`${basePath}/`}>Quick SDF Paint</a></dd></div>
          </dl>
        </header>

        <div className="article-frame page-shell">
          <aside className="article-toc" aria-label="目次">
            <strong>この記事の内容</strong>
            <ol>{toc.map((item) => <li key={item.id}><a href={`#${item.id}`}>{item.label}</a></li>)}</ol>
          </aside>
          <article className="article-body" id="article-body">{children}</article>
        </div>
      </main>

      <ArticleFooter current={article.slug} />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
    </>
  );
}

function formatArticleDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

export function EvidenceNote({ title = "この記事で独自に行ったこと", children }: { title?: string; children: ReactNode }) {
  return <aside className="evidence-note"><strong>{title}</strong><div>{children}</div></aside>;
}

export function ArticleFigure({
  src,
  alt,
  caption,
  width = 1920,
  height = 1001,
  contain = false,
  reducedMotionSrc,
}: {
  src: string;
  alt: string;
  caption: ReactNode;
  width?: number;
  height?: number;
  contain?: boolean;
  reducedMotionSrc?: string;
}) {
  return (
    <figure className={`article-figure${contain ? " contain" : ""}`}>
      <a href={src} target="_blank" rel="noreferrer" aria-label={`${alt}を原寸で開く`}>
        <picture>
          {reducedMotionSrc ? <source media="(prefers-reduced-motion: reduce)" srcSet={reducedMotionSrc} /> : null}
          {/* Static GitHub Pages export keeps the original research image byte-for-byte. */}
          <img src={src} alt={alt} width={width} height={height} loading="lazy" />
        </picture>
      </a>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

export function SourceList({ children }: { children: ReactNode }) {
  return <div className="source-list">{children}</div>;
}

export function RelatedArticles({ current }: { current: ArticleSlug }) {
  return (
    <section className="related-articles" aria-labelledby="related-title">
      <h2 id="related-title">関連する解説</h2>
      <div className="related-grid">
        {articles.filter((entry) => entry.slug !== current).map((entry) => (
          <a href={articlePath(entry.slug)} key={entry.slug}>
            <span>{entry.category}</span>
            <strong>{entry.shortTitle}</strong>
            <p>{entry.description}</p>
          </a>
        ))}
      </div>
    </section>
  );
}

export function ModelCredit() {
  return (
    <aside className="article-model-credit" aria-labelledby="article-model-credit-title">
      <div>
        <p>掲載キャラクター</p>
        <h2 id="article-model-credit-title">オリジナル3Dモデル「キプフェル（Kipfel）」</h2>
        <strong>モデル制作：かめ山　©もち山金魚</strong>
      </div>
      <p>この記事の操作画面と出力例に使用しています。モデルデータはQuick SDF Paintに含まれません。本プロジェクトは、もち山金魚／かめ山による公式・公認プロジェクトではありません。</p>
      <a href="https://mukumi.booth.pm/items/5813187" target="_blank" rel="noreferrer">公式商品ページ</a>
    </aside>
  );
}

function ArticleFooter({ current }: { current: ArticleSlug }) {
  return (
    <footer className="article-footer">
      <div className="page-shell">
        <RelatedArticles current={current} />
        <div className="article-footer-line">
          <span>Quick SDF Paint v0.7.1 — Blender 5.1 / Windows x64</span>
          <span><a href={`${basePath}/`}>操作ガイド</a> · <a href="https://github.com/yeczrtu/QuickSDFBlender">GitHub</a></span>
        </div>
      </div>
    </footer>
  );
}
