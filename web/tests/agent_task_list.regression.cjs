"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const handlebars = require("handlebars");
const {JSDOM} = require("jsdom");
const ts = require("typescript");

handlebars.registerHelper("t", (item) => item);

async function main() {
    const dom = new JSDOM("<body></body>", {url: "https://realm.test"});
    global.window = dom.window;
    global.document = dom.window.document;
    const $ = require("jquery");

    const view_calls = [];
    let jobs_by_view = {
        mine: [
            {id: "aaaaaaaa-0000-0000-0000-000000000001", status: "running", request: "First job"},
            {
                id: "aaaaaaaa-0000-0000-0000-000000000002",
                status: "completed",
                request: "x".repeat(120),
                title: "Custom short title",
            },
        ],
        waiting: [
            {
                id: "aaaaaaaa-0000-0000-0000-000000000003",
                status: "waiting_for_approval",
                request: "Needs a decision",
                needs_my_action: true,
            },
        ],
        running: [],
    };
    const api = {
        async list_jobs(_offset, view) {
            view_calls.push(view);
            return {jobs: jobs_by_view[view] ?? []};
        },
    };
    const overlay_opened = [];
    const overlays = {
        open_overlay(opts) {
            overlay_opened.push(opts.name);
            opts.$overlay.addClass("show");
        },
    };

    const transpile = (source) =>
        ts.transpileModule(source, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                esModuleInterop: true,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText;
    const out = {};
    vm.runInNewContext(
        transpile(fs.readFileSync(path.join(__dirname, "../src/agent_task_list.ts"), "utf8")),
        {
            exports: out,
            require(name) {
                if (name === "jquery") {
                    return $;
                }
                if (name === "./agent_api.ts") {
                    return api;
                }
                if (name === "./agent_job_panel.ts") {
                    return {job_hash: (id) => `#agent-jobs/${id}`};
                }
                if (name === "./agent_ui_state.ts") {
                    return {job_status_label: (status) => status};
                }
                if (name === "./browser_history.ts") {
                    return {exit_overlay() {}};
                }
                if (name === "./i18n.ts") {
                    // These fixed strings carry no interpolation values, so
                    // returning the default message matches the real $t().
                    return {$t: (descriptor) => descriptor.defaultMessage};
                }
                if (name === "./overlays.ts") {
                    return overlays;
                }
                if (name.endsWith(".hbs")) {
                    return () =>
                        handlebars.compile(
                            fs.readFileSync(
                                path.join(__dirname, "../templates/agent/job_list.hbs"),
                                "utf8",
                            ),
                        )({});
                }
                throw new Error(name);
            },
            window: dom.window,
            document: dom.window.document,
            console,
        },
    );

    const flush = async () => {
        for (let index = 0; index < 10; index += 1) {
            await Promise.resolve();
        }
    };

    try {
        out.open();
        await flush();

        assert.deepEqual(overlay_opened, ["agent-jobs-list"]);
        assert.deepEqual(view_calls, ["mine"]);
        assert.equal(
            $("#agent-job-list-tabs [data-agent-job-view='mine']").hasClass("selected"),
            true,
        );
        assert.equal($("#agent-job-list-empty").prop("hidden"), true);

        const $rows = $("#agent-job-list-rows a");
        assert.equal($rows.length, 2);
        assert.equal($rows.eq(0).attr("href"), "#agent-jobs/aaaaaaaa-0000-0000-0000-000000000001");
        assert.equal($rows.eq(0).find(".agent-job-row-text").text(), "First job");
        assert.equal($rows.eq(0).find(".agent-job-row-meta").text(), "running");
        // A job with no title falls back to the first 80 characters of the
        // request, the same rule the server uses.
        assert.equal($rows.eq(1).find(".agent-job-row-text").text(), "Custom short title");
        assert.equal($rows.eq(0).hasClass("needs-action"), false);

        // Switching to "Waiting for me" re-fetches with that view and
        // flags the row that needs the reader's action.
        $("#agent-job-list-tabs [data-agent-job-view='waiting']").trigger("click");
        await flush();
        assert.deepEqual(view_calls, ["mine", "waiting"]);
        assert.equal(
            $("#agent-job-list-tabs [data-agent-job-view='mine']").hasClass("selected"),
            false,
        );
        assert.equal(
            $("#agent-job-list-tabs [data-agent-job-view='waiting']").hasClass("selected"),
            true,
        );
        assert.equal($("#agent-job-list-rows a").length, 1);
        assert.equal($("#agent-job-list-rows a").hasClass("needs-action"), true);

        // Clicking the already-selected tab does not re-fetch.
        $("#agent-job-list-tabs [data-agent-job-view='waiting']").trigger("click");
        await flush();
        assert.deepEqual(view_calls, ["mine", "waiting"]);

        // An empty filter shows the empty state.
        $("#agent-job-list-tabs [data-agent-job-view='running']").trigger("click");
        await flush();
        assert.deepEqual(view_calls, ["mine", "waiting", "running"]);
        assert.equal($("#agent-job-list-rows a").length, 0);
        assert.equal($("#agent-job-list-empty").prop("hidden"), false);

        // Reopening the overlay resets the filter to "Mine".
        out.open();
        await flush();
        assert.deepEqual(view_calls, ["mine", "waiting", "running", "mine"]);
        assert.equal(
            $("#agent-job-list-tabs [data-agent-job-view='mine']").hasClass("selected"),
            true,
        );

        // A failed fetch clears the rows and reports the failure instead
        // of leaving the previous filter's rows on screen.
        jobs_by_view = new Proxy(jobs_by_view, {
            get(target, key) {
                if (key === "running") {
                    throw new Error("network error");
                }
                return target[key];
            },
        });
        $("#agent-job-list-tabs [data-agent-job-view='running']").trigger("click");
        await flush();
        assert.equal($("#agent-job-list-rows a").length, 0);
        assert.match($("#agent-job-list-status").text(), /unavailable/i);
    } finally {
        dom.window.close();
        delete global.window;
        delete global.document;
    }
}

void main()
    .then(() => process.stdout.write("Agent task list regressions passed.\n"))
    .catch((error) => {
        console.error(error);
        process.exitCode = 1;
    });
