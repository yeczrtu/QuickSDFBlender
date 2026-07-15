import type { MetadataRoute } from "next";
import { absoluteArticleUrl, articles, siteRoot } from "./articles/article-data";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  const siteModified = "2026-07-15";
  return [
    { url: siteRoot, lastModified: siteModified, changeFrequency: "monthly", priority: 1 },
    { url: `${siteRoot}articles/`, lastModified: siteModified, changeFrequency: "monthly", priority: 0.9 },
    ...articles.map((article) => ({
      url: absoluteArticleUrl(article.slug),
      lastModified: article.modified,
      changeFrequency: "yearly" as const,
      priority: 0.8,
    })),
  ];
}
