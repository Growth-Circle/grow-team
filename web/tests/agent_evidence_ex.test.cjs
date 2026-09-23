"use strict";

// web/tests/agent_evidence_ex.regression.cjs drives the real
// web/src/agent_job_panel.ts and web/src/agent_task_composer.ts through a
// jsdom + vm harness with a real jquery, the same technique
// web/tests/agent_job_handlers.regression.cjs uses. It must run as its own
// node process: this test suite's zjquery mock, loaded for every module
// under this ./lib/namespace.cjs harness, would otherwise intercept the
// real jquery the regression script needs.

const {execFileSync} = require("node:child_process");
const path = require("node:path");

const {run_test} = require("./lib/test.cjs");

run_test("agent job panel and composer evidence regressions (EX)", () => {
    execFileSync(process.execPath, [path.join(__dirname, "agent_evidence_ex.regression.cjs")], {
        cwd: path.join(__dirname, "../.."),
        encoding: "utf8",
        stdio: "pipe",
    });
});
