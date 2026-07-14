import { writeFile } from "node:fs/promises";

await writeFile(new URL("../out/.nojekyll", import.meta.url), "", "utf8");
