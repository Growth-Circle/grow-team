#!/usr/bin/env node
import {copyFile, mkdir} from "node:fs/promises";
import {createRequire} from "node:module";
import path from "node:path";
import {fileURLToPath} from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(root, "starlight_help/package.json"));
const sharp = require("sharp");
const source = path.join(root, "static/images/grow-team");

// Keep legacy paths for existing templates, API clients, and stored messages.
const vectors = {
    "static/images/favicon.svg": "icon.svg",
    "static/images/logo/zulip-icon-square.svg": "icon.svg",
    "static/images/logo/zulip-icon-circle.svg": "icon.svg",
    "static/images/logo/zulip-icon-bimi.svg": "bimi.svg",
    "static/images/logo/zulip-org-logo.svg": "wordmark.svg",
    "web/images/zulip-logo.svg": "wordmark.svg",
    "docs/images/zulip-logo.svg": "wordmark.svg",
    "web/images/logo/white-zulip-logo-without-text.svg": "icon-white.svg",
    "web/images/grow-team-wordmark-white.svg": "wordmark-white.svg",
    "web/images/emails/logo.svg": "email.svg",
};
for (const [target, input] of Object.entries(vectors)) {
    await copyFile(path.join(source, input), path.join(root, target));
}

const rasters = [
    ["static/images/favicon.png", "icon.svg", 64],
    ["static/images/logo/zulip-icon-128x128.png", "icon.svg", 128],
    ["static/images/logo/zulip-icon-512x512.png", "icon.svg", 512],
    ["static/images/logo/apple-touch-icon-precomposed.png", "icon.svg", 180],
    ["static/images/static_avatars/welcome-bot.png", "icon.svg", 100],
    ["static/images/static_avatars/welcome-bot-medium.png", "icon.svg", 500],
    ["static/images/static_avatars/notification-bot.png", "icon.svg", 100],
    ["static/images/static_avatars/notification-bot-medium.png", "icon.svg", 500],
    ["web/images/zulip-emoji/zulip.png", "icon.svg", 64],
    ["static/images/emails/email_logo.png", "email.svg", 800],
];
for (const [target, input, width] of rasters) {
    await mkdir(path.dirname(path.join(root, target)), {recursive: true});
    await sharp(path.join(source, input), {density: 192})
        .resize({width})
        .png()
        .toFile(path.join(root, target));
}
console.log(
    `Rendered ${rasters.length} PNG assets and copied ${Object.keys(vectors).length} SVG assets.`,
);
