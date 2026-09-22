"use strict";

const {execFileSync} = require("node:child_process");
const path = require("node:path");

const {run_test} = require("./lib/test.cjs");

for (const script of [
    "agent_settings_handlers.regression.cjs",
    "agent_job_handlers.regression.cjs",
]) {
    run_test(`real delegated handlers: ${script}`, () => {
        execFileSync(process.execPath, [path.join(__dirname, script)], {
            cwd: path.join(__dirname, "../.."),
            encoding: "utf8",
            stdio: "pipe",
        });
    });
}
