"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const $ = require("./lib/zjquery.cjs");
const {FakeJQuery} = require("./lib/zjquery_element.cjs");

mock_esm("../src/message_viewport", {height: () => 844});

const resize = zrequire("resize");

// zjquery makes a new wrapper on each call, so fake the sizes per
// element.
const boxes = new Map();

function set_box(selector, {display = "block", height, margin = 0}) {
    boxes.set($(selector)[0], {display, height, margin});
}

run_test("get_stream_filters_max_height", () => {
    const proto = FakeJQuery.prototype;
    const {css, outerHeight, not} = proto;
    proto.css = function (name) {
        return name === "display" ? boxes.get(this[0]).display : "0px";
    };
    proto.outerHeight = function (include_margin) {
        const box = boxes.get(this[0]);
        return box.height + (include_margin ? box.margin : 0);
    };
    proto.not = function () {
        return this;
    };
    try {
        set_box("#left-sidebar", {height: 844});
        set_box("#left-sidebar-workspace-switcher", {height: 78});
        set_box("#left-sidebar-command-search", {display: "none", height: 10});
        set_box("#left-sidebar-search", {height: 48});
        set_box("#left-sidebar-navigation-area", {height: 156, margin: 10});
        set_box("#sidebar-user-card", {height: 58, margin: 500});
        set_box("#left-sidebar-modal", {height: 40, margin: 300});

        // 844 - 78 - 48 - 166 - 58 - 40 - 15 (gap). The hidden box
        // and the margins of the user card and the modal add nothing.
        assert.equal(resize.get_stream_filters_max_height(), 439);
    } finally {
        Object.assign(proto, {css, outerHeight, not});
    }
});
