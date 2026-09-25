#!/usr/bin/env node
import {copyFile, mkdir, readFile} from "node:fs/promises";
import {createRequire} from "node:module";
import path from "node:path";
import {fileURLToPath} from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const require = createRequire(path.join(root, "starlight_help/package.json"));
const sharp = require("sharp");

const source = path.join(root, "static/images/sanji");

// Safe zone check for the maskable icon (see BRANDING.md), checked before
// any file is written. The mark's farthest point from its own center
// (24, 24) is 25.31 units of its 48-unit box. Placed on the maskable
// canvas by the source SVG's own translate and scale, it must stay
// inside Android's safe circle, radius 33/108 of the canvas.
const maskableSource = await readFile(path.join(source, "app-icon-maskable.svg"), "utf8");
const [, maskableTx, maskableTy, maskableScale] = maskableSource
    .match(/translate\(([\d.]+) ([\d.]+)\) scale\(([\d.]+)\)/)
    .map(Number);
const maskableCanvas = Number(maskableSource.match(/viewBox="0 0 (\d+) \d+"/)[1]);
const markFarthestPoint = 25.31;
const androidSafeRatio = 33 / 108;
const maskableReach =
    Math.hypot(
        maskableTx + 24 * maskableScale - maskableCanvas / 2,
        maskableTy + 24 * maskableScale - maskableCanvas / 2,
    ) +
    markFarthestPoint * maskableScale;
const maskableSafeRatio = maskableReach / maskableCanvas;
if (maskableSafeRatio > androidSafeRatio) {
    throw new Error(
        `app-icon-maskable.svg mark reaches ${(maskableSafeRatio * 100).toFixed(1)}% of the canvas, past Android's ${(androidSafeRatio * 100).toFixed(1)}% safe circle.`,
    );
}

// Keep legacy paths for existing templates, API clients, and stored messages.
const vectors = {
    "static/images/favicon.svg": "favicon.svg",
    "static/images/logo/zulip-icon-square.svg": "app-icon.svg",
    "static/images/logo/zulip-icon-circle.svg": "mark.svg",
    "static/images/logo/zulip-icon-bimi.svg": "bimi.svg",
    "static/images/logo/zulip-org-logo.svg": "lockup.svg",
    "static/images/logo/zulip-org-logo-night.svg": "lockup-dark.svg",
    "web/images/zulip-logo.svg": "lockup.svg",
    "docs/images/zulip-logo.svg": "lockup.svg",
    "web/images/logo/white-zulip-logo-without-text.svg": "mark-on-dark.svg",
    "web/images/grow-team-wordmark-white.svg": "lockup-dark.svg",
    "web/images/emails/logo.svg": "email.svg",
};
for (const [target, input] of Object.entries(vectors)) {
    await copyFile(path.join(source, input), path.join(root, target));
}

const rasters = [
    ["static/images/favicon.png", "favicon.svg", 64],
    ["static/images/logo/zulip-icon-128x128.png", "app-icon-full.svg", 128],
    ["static/images/logo/zulip-icon-512x512.png", "app-icon.svg", 512],
    ["static/images/logo/apple-touch-icon-precomposed.png", "app-icon-full.svg", 180],
    ["static/images/logo/sanji-icon-192.png", "app-icon.svg", 192],
    ["static/images/logo/sanji-icon-maskable-512.png", "app-icon-maskable.svg", 512],
    ["static/images/static_avatars/welcome-bot.png", "app-icon-full.svg", 100],
    ["static/images/static_avatars/welcome-bot-medium.png", "app-icon-full.svg", 500],
    ["static/images/static_avatars/notification-bot.png", "app-icon-full.svg", 100],
    ["static/images/static_avatars/notification-bot-medium.png", "app-icon-full.svg", 500],
    ["web/images/zulip-emoji/zulip.png", "mark.svg", 64],
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
