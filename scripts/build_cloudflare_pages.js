const fs = require("fs");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const sourceDir = path.join(rootDir, "frontend");
const outputDir = path.join(rootDir, "dist");
const apiBaseUrl = (process.env.API_BASE_URL || "").trim().replace(/\/+$/, "");

function copyRecursive(fromDir, toDir) {
  fs.mkdirSync(toDir, { recursive: true });
  for (const entry of fs.readdirSync(fromDir, { withFileTypes: true })) {
    const fromPath = path.join(fromDir, entry.name);
    const toPath = path.join(toDir, entry.name);
    if (entry.isDirectory()) {
      copyRecursive(fromPath, toPath);
      continue;
    }
    fs.copyFileSync(fromPath, toPath);
  }
}

fs.rmSync(outputDir, { recursive: true, force: true });
fs.mkdirSync(outputDir, { recursive: true });
copyRecursive(sourceDir, outputDir);

const runtimeConfigPath = path.join(outputDir, "h5", "runtime-config.js");
const runtimeConfig = [
  "window.__WC_PREDICT_CONFIG__ = window.__WC_PREDICT_CONFIG__ || {};",
  `window.__WC_PREDICT_CONFIG__.apiBaseUrl = ${JSON.stringify(apiBaseUrl)};`,
  "",
].join("\n");

fs.writeFileSync(runtimeConfigPath, runtimeConfig, "utf8");
console.log(`Cloudflare Pages bundle written to ${outputDir}`);
