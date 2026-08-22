#!/usr/bin/env node

import { readFile, realpath } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { parseHTML } from "linkedom";
import { Defuddle } from "defuddle/node";

const input = process.argv[2];
if (!input) {
  console.error("usage: defuddle_adapter.mjs <html-file>");
  process.exit(2);
}

try {
  const htmlPath = await realpath(input);
  const html = await readFile(htmlPath, "utf8");
  const { document } = parseHTML(html);
  const result = await Defuddle(document, pathToFileURL(htmlPath).href, {
    contentSelector: "main",
    markdown: true,
    useAsync: false,
    removeSmallImages: false,
  });
  const content = typeof result.content === "string" ? result.content : "";
  if (!content.trim()) {
    throw new Error("no Markdown content extracted from <main>");
  }
  process.stdout.write(
    JSON.stringify({
      content,
      title: result.title ?? null,
      wordCount: result.wordCount ?? null,
      imageCount: (content.match(/data:image\//g) || []).length,
    }) + "\n",
  );
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
