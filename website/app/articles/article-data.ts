export const siteOrigin = "https://yeczrtu.github.io";
export const basePath = "/QuickSDFBlender";
export const siteRoot = `${siteOrigin}${basePath}/`;

export type ArticleSlug =
  | "face-shadow-threshold-map"
  | "sdf-threshold-interpolation"
  | "blender-threshold-map-workflow";

export type ArticleRecord = {
  slug: ArticleSlug;
  title: string;
  shortTitle: string;
  description: string;
  audience: string;
  readingTime: string;
  category: string;
  published: string;
  modified: string;
  image: string;
};

export const articles: readonly ArticleRecord[] = [
  {
    slug: "face-shadow-threshold-map",
    title: "顔影スレッショルドマップとは：仕組みと制作方法",
    shortTitle: "顔影スレッショルドマップとは",
    description:
      "トゥーン顔影を光の向きで切り替えるスレッショルドマップについて、保存する値、制作工程、ほかの顔影手法との違い、適用範囲を整理します。",
    audience: "アーティスト／テクニカルアーティスト",
    readingTime: "約12分",
    category: "基礎と全体像",
    published: "2026-07-15",
    modified: "2026-07-15",
    image: `${basePath}/research/threshold-study/threshold-map-overview-card.png`,
  },
  {
    slug: "sdf-threshold-interpolation",
    title: "角度別マスクを1枚にまとめる方法：SDF距離補間の比較",
    shortTitle: "SDF距離補間の比較",
    description:
      "7枚の二値マスクを使い、最近傍、画素線形、正規化ボックスブラー、SDF距離比を同一条件で比較します。境界移動、誤差、8-bitと16-bitの量子化を実測します。",
    audience: "テクニカルアーティスト／ツール開発者",
    readingTime: "約15分",
    category: "アルゴリズムと検証",
    published: "2026-07-15",
    modified: "2026-07-15",
    image: `${basePath}/research/threshold-study/method-comparison.png`,
  },
  {
    slug: "blender-threshold-map-workflow",
    title: "Quick SDF Paint 0.7.1で顔影スレッショルドマップを作る：Blenderでの実践手順",
    shortTitle: "Quick SDF Paint 0.7.1の実践手順",
    description:
      "Quick SDF Paint 0.7.1で法線から作った影ガイドを修正し、16-bitの顔影スレッショルドマップを書き出す手順を説明します。UV、正面軸、ミラーの問題も切り分けます。",
    audience: "Blenderを使うアーティスト",
    readingTime: "約18分",
    category: "Blender実践",
    published: "2026-07-15",
    modified: "2026-07-15",
    image: `${basePath}/media/quick-sdf-studio-overview.png`,
  },
] as const;

export function articlePath(slug: ArticleSlug) {
  return `${basePath}/articles/${slug}/`;
}

export function absoluteArticleUrl(slug: ArticleSlug) {
  return `${siteOrigin}${articlePath(slug)}`;
}

export function getArticle(slug: ArticleSlug) {
  const article = articles.find((entry) => entry.slug === slug);
  if (!article) throw new Error(`Unknown article: ${slug}`);
  return article;
}
