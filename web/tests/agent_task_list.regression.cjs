"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ts = require("typescript");

// The old task list at #agent-jobs is gone. The module keeps its `open`
// export, because hashchange.ts calls it for that address, and it sends
// the person to Today without a new history entry.
function main() {
    const replaced = [];
    const out = {};
    vm.runInNewContext(
        ts.transpileModule(
            fs.readFileSync(path.join(__dirname, "../src/agent_task_list.ts"), "utf8"),
            {
                compilerOptions: {
                    module: ts.ModuleKind.CommonJS,
                    esModuleInterop: true,
                    target: ts.ScriptTarget.ES2022,
                },
            },
        ).outputText,
        {
            exports: out,
            window: {location: {replace: (hash) => replaced.push(hash)}},
        },
    );
    out.open();
    assert.deepEqual(replaced, ["#today"]);

    // The hash change tests replace `open` by name.
    const calls = [];
    out.rewire_open(() => calls.push("stub"));
    out.open();
    assert.deepEqual(calls, ["stub"]);
    assert.deepEqual(replaced, ["#today"]);
}

main();
