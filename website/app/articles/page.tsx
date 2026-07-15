import type { Metadata } from "next";
import { articlePath, articles, basePath, siteOrigin } from "./article-data";

const pageUrl = `${siteOrigin}${basePath}/articles/`;

export const metadata: Metadata = {
  title: "顔影スレッショルドマップ解説 | Quick SDF Paint",
  description:
    "顔影スレッショルドマップの仕組み、SDF距離補間の比較検証、Blenderでの実践手順を、アーティスト向けに体系化した解説記事です。",
  alternates: { canonical: pageUrl },
  openGraph: {
    title: "顔影スレッショルドマップ解説 | Quick SDF Paint",
    description: "基礎、補間アルゴリズム、Blender実践の3つに分けて解説します。",
    type: "website",
    url: pageUrl,
    images: [{
      url: `${siteOrigin}${basePath}/research/threshold-study/method-comparison.png`,
      width: 2592,
      height: 1552,
      alt: "角度別マスクの補間方法を同じ入力で比較した検証画像",
    }],
  },
  twitter: {
    card: "summary_large_image",
    title: "顔影スレッショルドマップ解説 | Quick SDF Paint",
    description: "基礎、補間アルゴリズム、Blender実践の3つに分けて解説します。",
    images: [`${siteOrigin}${basePath}/research/threshold-study/method-comparison.png`],
  },
};

export default function ArticlesIndex() {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: "顔影スレッショルドマップ解説",
    url: pageUrl,
    inLanguage: "ja-JP",
    hasPart: articles.map((article) => ({
      "@type": "TechArticle",
      headline: article.title,
      url: `${siteOrigin}${articlePath(article.slug)}`,
    })),
  };

  return (
    <>
      <a className="skip-link" href="#articles">記事一覧へ移動</a>
      <header className="article-site-header">
        <a className="brand" href={`${basePath}/`} aria-label="Quick SDF Paint 操作ガイド">
          <span className="brand-mark" aria-hidden="true" />
          <span>Quick SDF Paint</span>
          <small>解説記事</small>
        </a>
        <nav aria-label="サイトナビゲーション">
          <a aria-current="page" href={`${basePath}/articles/`}>記事一覧</a>
          <a href={`${basePath}/`}>操作ガイド</a>
          <a href="https://github.com/yeczrtu/QuickSDFBlender">GitHub</a>
        </nav>
      </header>

      <main id="articles">
        <header className="articles-index-hero page-shell">
          <p className="article-category">Quick SDF Paint technical notes</p>
          <h1>顔影スレッショルドマップを<br />仕組みから理解する</h1>
          <p>角度別の白黒マスクを、光の向きに応じた一枚のデータへ変換する手法を扱います。基礎構造、同一条件での補間比較、Blender 5.1での実践手順を順番に説明します。</p>
        </header>

        <section className="articles-index-grid page-shell" aria-label="解説記事">
          {articles.map((article, index) => (
            <a className="article-index-card" href={articlePath(article.slug)} key={article.slug}>
              <span className="article-index-number">0{index + 1}</span>
              <span className="article-index-category">{article.category}</span>
              <h2>{article.shortTitle}</h2>
              <p>{article.description}</p>
              <dl>
                <div><dt>対象</dt><dd>{article.audience}</dd></div>
                <div><dt>読了</dt><dd>{article.readingTime}</dd></div>
              </dl>
              <strong>記事を読む <span aria-hidden="true">→</span></strong>
            </a>
          ))}
        </section>

        <section className="articles-method page-shell" aria-labelledby="articles-method-title">
          <h2 id="articles-method-title">調査と検証の範囲</h2>
          <div>
            <p>公開資料から用語、既存事例、アルゴリズムを確認し、複数実装に共通する処理を制作工程に沿って整理しています。</p>
            <p>比較記事の画像と数値は、リポジトリ内の決定的な検証スクリプトから生成しています。実践記事はBlender 5.1とQuick SDF Paint 0.7.1で確認した内容です。</p>
          </div>
        </section>
      </main>

      <footer className="site-footer"><div className="page-shell"><span>Quick SDF Paint 解説記事</span><span><a href={`${basePath}/`}>操作ガイドへ戻る</a></span></div></footer>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
    </>
  );
}
