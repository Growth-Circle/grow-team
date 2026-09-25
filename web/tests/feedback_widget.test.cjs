"use strict";

const assert = require("node:assert/strict");

const {clock, mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test, noop} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");

const modals = mock_esm("../src/modals", {
    any_active: () => false,
});
const drawer = mock_esm("../src/drawer", {
    is_open: () => false,
});

const feedback_widget = zrequire("feedback_widget");

// zjquery gives the same element for each $("<div>") call in one
// test. Thus $("<div>") in a test is the root of the toast that
// show_toast made. This function prepares that root for the timer:
// clear_toast() calls remove() on it, and the timer asks if the
// pointer or the focus is on it.
function prepare_toast_root() {
    const $toast = $("<div>");
    $toast[0].remove = noop;
    $toast.set_matches(":hover, :focus-within", false);
    return $toast;
}

// A visible slot for an inline toast: it has a layout box. The test
// sees each element that show_toast adds to the slot.
function make_inline_slot(name) {
    const $slot = $.create(name);
    $slot[0].getClientRects = () => [{width: 600, height: 42}];
    const appended = [];
    $slot[0].append = (...elements) => {
        appended.push(...elements.map((element) => element.to_$()));
    };
    return {$slot, appended};
}

run_test("global success toast", () => {
    feedback_widget.reset_for_testing();
    feedback_widget.show_toast({text: "Workspace settings saved."});

    const $toast = $("<div>");
    assert.ok($toast.hasClass("sj-toast"));
    assert.ok($toast.hasClass("sj-toast--global"));
    assert.ok($toast.hasClass("sj-toast--success"));
    // The visible toast is not a live region. The status region below
    // gives the text to screen readers.
    assert.equal($toast.attr("role"), undefined);

    const $icon = $("<i>");
    assert.ok($icon.hasClass("sj-toast__icon"));
    assert.equal($icon.attr("aria-hidden"), "true");
    assert.equal($icon.text(), "✓");
    assert.equal($("<span>").text(), "Workspace settings saved.");
});

run_test("info toast has no icon and no action", () => {
    feedback_widget.reset_for_testing();
    feedback_widget.show_toast({text: "Pindah ke Studio Arunika.", variant: "info"});

    const $toast = $("<div>");
    assert.ok($toast.hasClass("sj-toast--global"));
    assert.ok($toast.hasClass("sj-toast--info"));
    assert.ok(!$("<i>").hasClass("sj-toast__icon"));
    assert.throws(() => $("<button>").get_on_handler("click"));
});

run_test("error toast without on_retry has no action", () => {
    feedback_widget.reset_for_testing();
    feedback_widget.show_toast({text: "Gagal menyimpan.", variant: "error"});

    assert.ok($("<div>").hasClass("sj-toast--error"));
    assert.ok(!$("<i>").hasClass("sj-toast__icon"));
    assert.throws(() => $("<button>").get_on_handler("click"));
});

run_test("Try again runs on_retry and removes the toast", () => {
    feedback_widget.reset_for_testing();
    let retried = false;
    feedback_widget.show_toast({
        text: "Gagal menyimpan.",
        variant: "error",
        on_retry() {
            retried = true;
        },
    });

    const $button = $("<button>");
    assert.equal($button.text(), "translated: Try again");

    let removed = false;
    $("<div>")[0].remove = () => {
        removed = true;
    };
    $button.get_on_handler("click")();
    assert.ok(retried);
    assert.ok(removed);
});

run_test("default duration is 3.2 seconds", () => {
    feedback_widget.reset_for_testing();
    feedback_widget.show_toast({text: "Pindah ke Studio Arunika."});
    const $toast = prepare_toast_root();
    let removed_count = 0;
    $toast[0].remove = () => {
        removed_count += 1;
    };

    clock.tick(3199);
    assert.equal(removed_count, 0);
    clock.tick(1);
    assert.equal(removed_count, 1);
});

run_test("duration is 5 seconds with an Undo button", () => {
    feedback_widget.reset_for_testing();
    feedback_widget.show_toast({text: "'Tugas' dipindah ke Review.", on_undo: noop});
    const $toast = prepare_toast_root();
    let removed_count = 0;
    $toast[0].remove = () => {
        removed_count += 1;
    };

    clock.tick(4999);
    assert.equal(removed_count, 0);
    clock.tick(1);
    assert.equal(removed_count, 1);
});

run_test("the toast stays while the pointer or the focus is on it", () => {
    feedback_widget.reset_for_testing();
    feedback_widget.show_toast({text: "'Tugas' dipindah ke Review.", on_undo: noop});
    const $toast = prepare_toast_root();
    let removed_count = 0;
    $toast[0].remove = () => {
        removed_count += 1;
    };

    $toast.set_matches(":hover, :focus-within", true);
    clock.tick(5000);
    assert.equal(removed_count, 0);

    $toast.set_matches(":hover, :focus-within", false);
    clock.tick(5000);
    assert.equal(removed_count, 1);
});

run_test("a new toast removes the old Undo and does not run it", () => {
    feedback_widget.reset_for_testing();
    let old_undo_ran = false;
    feedback_widget.show_toast({
        text: "'Tugas lama' dipindah ke Review.",
        // The test checks that this callback does not run.
        /* istanbul ignore next */
        on_undo() {
            old_undo_ran = true;
        },
    });
    const $old_button = $("<button>");
    assert.equal($old_button.text(), "translated: Undo");

    // The next toast removes this toast. Then clear the zjquery cache,
    // thus the next toast gets new elements.
    let old_toast_removed = false;
    $("<div>")[0].remove = () => {
        old_toast_removed = true;
    };
    $.clear_all_elements();

    let new_undo_ran = false;
    feedback_widget.show_toast({
        text: "'Tugas baru' dipindah ke Selesai.",
        on_undo() {
            new_undo_ran = true;
        },
    });
    assert.ok(old_toast_removed);

    prepare_toast_root();
    $("<button>").get_on_handler("click")();
    assert.equal(old_undo_ran, false);
    assert.equal(new_undo_ran, true);
});

run_test("Undo runs the callback, then shows a Cancelled. toast", () => {
    feedback_widget.reset_for_testing();
    let undo_ran = false;
    feedback_widget.show_toast({
        text: "'Tugas' dipindah ke Review.",
        on_undo() {
            undo_ran = true;
        },
    });
    const $undo = $("<button>");
    prepare_toast_root();
    $.clear_all_elements();

    $undo.get_on_handler("click")();

    assert.ok(undo_ran);
    assert.ok($("<div>").hasClass("sj-toast--global"));
    assert.equal($("<span>").text(), "translated: Cancelled.");
});

run_test("inline toast with Undo in a visible slot", () => {
    feedback_widget.reset_for_testing();
    const {$slot, appended} = make_inline_slot("inline-slot-stub-1");
    let undo_ran = false;

    feedback_widget.show_toast({
        text: "'Tugas' dipindah ke Review.",
        inline_container: $slot,
        on_undo() {
            undo_ran = true;
        },
    });

    assert.equal(appended.length, 1);
    assert.ok(appended[0].hasClass("sj-toast--inline"));
    assert.ok(!appended[0].hasClass("sj-toast--global"));
    assert.ok($("<i>").hasClass("sj-toast__icon"));
    const $undo = $("<button>");
    assert.equal($undo.text(), "translated: Undo");

    // The Cancelled. toast after Undo goes to the same slot.
    prepare_toast_root();
    $.clear_all_elements();
    $undo.get_on_handler("click")();
    assert.ok(undo_ran);
    assert.equal(appended.length, 2);
    assert.ok(appended[1].hasClass("sj-toast--inline"));
});

run_test("a hidden slot gets no inline toast", () => {
    feedback_widget.reset_for_testing();
    const $slot = $.create("inline-slot-stub-2");
    $slot[0].getClientRects = () => [];

    feedback_widget.show_toast({text: "Semua beres.", inline_container: $slot});

    assert.ok($("<div>").hasClass("sj-toast--global"));
    // empty() was not called on the slot.
    assert.equal($slot[0].innerHTML, "never-been-set");
});

run_test("an error toast is never inline", () => {
    feedback_widget.reset_for_testing();
    const {$slot, appended} = make_inline_slot("inline-slot-stub-3");

    feedback_widget.show_toast({
        text: "Gagal menyimpan.",
        variant: "error",
        inline_container: $slot,
    });

    assert.ok($("<div>").hasClass("sj-toast--global"));
    assert.equal(appended.length, 0);
});

run_test("no inline toast while a modal or a drawer is open", ({override}) => {
    feedback_widget.reset_for_testing();
    const {$slot, appended} = make_inline_slot("inline-slot-stub-4");

    override(modals, "any_active", () => true);
    feedback_widget.show_toast({text: "Ruang # riset-churn dibuat.", inline_container: $slot});
    assert.ok($("<div>").hasClass("sj-toast--global"));

    override(modals, "any_active", () => false);
    override(drawer, "is_open", () => true);
    prepare_toast_root();
    feedback_widget.show_toast({text: "Ruang # riset-churn dibuat.", inline_container: $slot});
    assert.ok($("<div>").hasClass("sj-toast--global"));
    assert.equal(appended.length, 0);
});

run_test("the status region gets the text a short time later", () => {
    feedback_widget.reset_for_testing();
    const $status = $("#sj-toast-status");

    feedback_widget.show_toast({text: "Pengaturan workspace disimpan."});
    assert.equal($status.text(), "");
    clock.tick(100);
    assert.equal($status.text(), "Pengaturan workspace disimpan.");

    // The same text again is cleared, then written again.
    prepare_toast_root();
    feedback_widget.show_toast({text: "Pengaturan workspace disimpan."});
    assert.equal($status.text(), "");
    clock.tick(100);
    assert.equal($status.text(), "Pengaturan workspace disimpan.");

    // A new toast replaces a text that is not written yet.
    prepare_toast_root();
    feedback_widget.show_toast({text: "Ruang # rilis dibuat."});
    clock.tick(50);
    prepare_toast_root();
    feedback_widget.show_toast({text: "Ruang # riset dibuat."});
    clock.tick(50);
    assert.equal($status.text(), "");
    clock.tick(50);
    assert.equal($status.text(), "Ruang # riset dibuat.");
});

run_test("the first toast adds the status region to the page", () => {
    feedback_widget.reset_for_testing();
    $.create("#sj-toast-status", {elements: []});
    let appended_count = 0;
    $("body")[0].append = () => {
        appended_count += 1;
    };

    feedback_widget.show_toast({text: "Workspace settings saved."});

    // The page got the toast and the status region. zjquery gives the
    // same element for the two, because both come from $("<div>").
    assert.equal(appended_count, 2);
    const $region = $("<div>");
    assert.equal($region.attr("id"), "sj-toast-status");
    assert.equal($region.attr("role"), "status");
    assert.ok($region.hasClass("sj-toast-status"));
    clock.tick(100);
    assert.equal($region.text(), "Workspace settings saved.");
});
