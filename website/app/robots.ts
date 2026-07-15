import type { MetadataRoute } from "next";
import { siteRoot } from "./articles/article-data";

export const dynamic = "force-static";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/" },
    sitemap: `${siteRoot}sitemap.xml`,
  };
}
