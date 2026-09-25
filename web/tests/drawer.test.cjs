"use strict";

const assert = require("node:assert/strict");

const {mock_esm, set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

set_global("document", {});
// drawer.ts gives focus back to document.body when no element had
// focus before the drawer opened.
document.body = $.create("document-body-stub")[0];
document.body.isConnected = true;

const modals = mock_esm("../src/modals", {
    open: noop,
    close: noop,
});

const drawer = zrequire("drawer");

// drawer.hbs renders for real. zjquery gives one element for each
// exact HTML string, thus the test can prepare the slots of each frame
// before drawer.ts looks for them. Each frame keeps the elements that
// drawer.ts adds to its body and to its footer.
function prepare_frames(mock_template) {
    const frames = [];
    mock_template("drawer.hbs", true, (data, html) => {
        const id = data.drawer_unique_id;
        const frame = {
            data,
            html,
            $close: $.create(`close-stub-${id}`),
            body_children: [],
            footer_children: [],
        };
        const $body_slot = $.create(`body-slot-stub-${id}`);
        $body_slot[0].append = (...elements) => {
            frame.body_children.push(...elements);
        };
        const $footer_slot = $.create(`footer-slot-stub-${id}`);
        $footer_slot[0].append = (...elements) => {
            frame.footer_children.push(...elements.map((element) => element.to_$()));
        };
        const $frame = $(html);
        $frame.set_find_results(".sj-drawer__body", $body_slot);
        $frame.set_find_results(".sj-drawer__footer", $footer_slot);
        $frame.set_find_results(".sj-drawer__close", frame.$close);
        frames.push(frame);
        return html;
    });
    return frames;
}

// Keeps the conf of each modals.open() call. The tests run the conf
// callbacks in the order that modals.ts runs them.
function capture_modals_open(override) {
    const opened = [];
    override(modals, "open", (modal_id, conf) => {
        opened.push({modal_id, conf});
    });
    return opened;
}

function make_trigger(name, {connected}) {
    const $trigger = $.create(name);
    $trigger[0].isConnected = connected;
    return $trigger;
}

run_test("open() renders the frame and gives it to modals.open()", ({mock_template, override}) => {
    document.activeElement = undefined;
    const frames = prepare_frames(mock_template);
    const opened = capture_modals_open(override);
    const $body = $.create("caller-body-stub-1");

    drawer.open({kind_label: "TUGAS · #12", $body});

    const [frame] = frames;
    assert.equal(frame.data.kind_label, "TUGAS · #12");
    assert.ok(frame.html.includes('class="micromodal sj-drawer"'));
    assert.ok(frame.html.includes("modal__overlay"));
    assert.ok(frame.html.includes('aria-label="translated: Close"'));
    // Micromodal closes the drawer on Esc and on a click on this ✕.
    assert.ok(frame.html.includes("data-micromodal-close"));
    assert.deepEqual(frame.body_children, [$body[0]]);
    assert.equal(frame.footer_children.length, 0);

    assert.equal(opened.length, 1);
    assert.equal(opened[0].modal_id, frame.data.drawer_unique_id);
    assert.equal(opened[0].conf.autoremove, true);
    assert.equal(drawer.is_open(), false, "not open before on_show");
});

run_test("is_open() is true from on_show until on_hidden", ({mock_template, override}) => {
    document.activeElement = undefined;
    prepare_frames(mock_template);
    const opened = capture_modals_open(override);
    let closed = false;

    drawer.open({
        kind_label: "RAPAT · FATHOM",
        $body: $.create("caller-body-stub-2"),
        on_close() {
            closed = true;
        },
    });
    const {conf} = opened[0];

    conf.on_show();
    assert.equal(drawer.is_open(), true);
    conf.on_shown();
    assert.equal(drawer.is_open(), true);
    assert.equal(closed, false);

    conf.on_hidden();
    assert.equal(drawer.is_open(), false);
    assert.ok(closed);
});

run_test("focus goes into the drawer, then back to the trigger", ({mock_template, override}) => {
    const frames = prepare_frames(mock_template);
    const opened = capture_modals_open(override);
    const $trigger = make_trigger("trigger-stub-1", {connected: true});
    document.activeElement = $trigger[0];

    drawer.open({kind_label: "JOB AGEN", $body: $.create("caller-body-stub-3")});
    const {conf} = opened[0];

    conf.on_show();
    assert.ok(frames[0].$close.is_focused());
    assert.ok(!$trigger.is_focused());

    conf.on_shown();
    conf.on_hidden();
    assert.ok($trigger.is_focused());
});

run_test("focus does not go to a trigger that is out of the page", ({mock_template, override}) => {
    prepare_frames(mock_template);
    const opened = capture_modals_open(override);
    const $trigger = make_trigger("trigger-stub-2", {connected: false});
    document.activeElement = $trigger[0];

    drawer.open({kind_label: "JOB AGEN", $body: $.create("caller-body-stub-4")});
    const {conf} = opened[0];
    conf.on_show();
    conf.on_shown();
    conf.on_hidden();

    assert.ok(!$trigger.is_focused());
});

run_test("a drawer from a drawer keeps the first trigger", ({mock_template, override}) => {
    const frames = prepare_frames(mock_template);
    const opened = capture_modals_open(override);
    const $trigger = make_trigger("trigger-stub-3", {connected: true});
    document.activeElement = $trigger[0];
    let first_closed = false;

    drawer.open({
        kind_label: "TUGAS · #12",
        $body: $.create("caller-body-stub-5"),
        on_close() {
            first_closed = true;
        },
    });
    const first = opened[0].conf;
    first.on_show();
    first.on_shown();

    // The focus is now on the ✕ of the first drawer. A button in the
    // first drawer opens the second drawer.
    const $first_close = frames[0].$close;
    $first_close[0].isConnected = false;
    document.activeElement = $first_close[0];
    drawer.open({kind_label: "JOB AGEN", $body: $.create("caller-body-stub-6")});
    const second = opened[1].conf;

    // modals.open() hides the open drawer before it shows the new one.
    first.on_hidden();
    assert.ok(first_closed);
    second.on_show();
    second.on_shown();
    assert.equal(drawer.is_open(), true);

    $trigger.trigger("blur");
    second.on_hidden();
    assert.equal(drawer.is_open(), false);
    assert.ok($trigger.is_focused());
});

run_test("close() waits for the end of the open animation", ({mock_template, override}) => {
    document.activeElement = undefined;
    prepare_frames(mock_template);
    const opened = capture_modals_open(override);
    const closed_ids = [];
    override(modals, "close", (modal_id) => {
        closed_ids.push(modal_id);
    });

    // Nothing is open: close() does nothing.
    drawer.close();
    assert.deepEqual(closed_ids, []);

    drawer.open({kind_label: "FILE · GOOGLE DRIVE", $body: $.create("caller-body-stub-7")});
    const {modal_id, conf} = opened[0];
    conf.on_show();

    // modals.close() has no effect before the animation ends.
    drawer.close();
    assert.deepEqual(closed_ids, []);
    conf.on_shown();
    assert.deepEqual(closed_ids, [modal_id]);

    // After the animation, close() closes at once.
    drawer.close();
    assert.deepEqual(closed_ids, [modal_id, modal_id]);
    conf.on_hidden();
});

run_test("footer actions: a plain button and a primary button", ({mock_template}) => {
    document.activeElement = undefined;
    const frames = prepare_frames(mock_template);

    drawer.open({
        kind_label: "TUGAS · #12",
        $body: $.create("caller-body-stub-8"),
        actions: [{label: "Hapus", on_click: noop}],
    });
    const [$plain] = frames[0].footer_children;
    assert.ok($plain.hasClass("sj-drawer__action"));
    assert.ok(!$plain.hasClass("sj-drawer__action--primary"));
    assert.equal($plain.attr("type"), "button");
    assert.equal($plain.text(), "Hapus");

    // zjquery gives the same element for each $("<button>") call.
    // Reset it, thus the next drawer gets a new button.
    $.reset_selector("<button>");
    let approved = false;
    drawer.open({
        kind_label: "TUGAS · #12",
        $body: $.create("caller-body-stub-9"),
        actions: [
            {
                label: "Approve hasil",
                primary: true,
                on_click() {
                    approved = true;
                },
            },
        ],
    });
    const [$primary] = frames[1].footer_children;
    assert.ok($primary.hasClass("sj-drawer__action--primary"));
    assert.equal($primary.text(), "Approve hasil");
    $primary.get_on_handler("click")();
    assert.ok(approved);
});
