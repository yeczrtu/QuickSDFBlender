import type { MetadataRoute } from "next";
import { absoluteArticleUrl, articles, siteRoot } from "./articles/article-data";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  const latestArticleModified = articles.reduce(
    (latest, article) => article.modified > latest ? article.modified : latest,
    articles[0].modified,
  );
  return [
    { url: siteRoot, lastModified: latestArticleModified, changeFrequency: "monthly", priority: 1 },
    { url: `${siteRoot}articles/`, lastModified: latestArticleModified, changeFrequency: "monthly", priority: 0.9 },
    ...articles.map((article) => ({
      url: absoluteArticleUrl(article.slug),
      lastModified: article.modified,
      changeFrequency: "yearly" as const,
      priority: 0.8,
    })),
  ];
}
