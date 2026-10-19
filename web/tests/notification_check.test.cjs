"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

let next_response;
let posted;
mock_esm("../src/channel", {
    post(options) {
        posted = options;
        return Promise.resolve(next_response);
    },
});

let permission = "granted";
let requested = 0;
const desktop_notifications = mock_esm("../src/desktop_notifications", {
    permission_state: () => permission,
    request_desktop_notifications_permission() {
        requested += 1;
        permission = "granted";
        return Promise.resolve("granted");
    },
});
const shown = [];
mock_esm("../src/message_notifications", {
    send_test_notification(content) {
        shown.push(content);
    },
});
let is_mobile = false;
mock_esm("../src/util", {is_mobile: () => is_mobile});

const notification_check = zrequire("notification_check");

function push_check(overrides) {
    return {
        push_service_configured: true,
        device_count: 1,
        sent: true,
        error: null,
        ...overrides,
    };
}

run_test("the browser is set up only when it allows notifications", () => {
    assert.equal(notification_check.browser_status("granted", false).ok, true);
    const denied = notification_check.browser_status("denied", false);
    assert.equal(denied.ok, false);
    assert.match(denied.text, /site settings/);
    assert.equal(notification_check.browser_status("default", false).ok, false);
    // A phone browser cannot show them, whatever it says.
    assert.equal(notification_check.browser_status("granted", true).ok, false);
});

run_test("the phone says what is missing, in the order the person fixes it", () => {
    const none = notification_check.phone_status(push_check({push_service_configured: false}));
    assert.equal(none.ok, false);
    assert.match(none.text, /no push service/);

    const no_device = notification_check.phone_status(push_check({device_count: 0, sent: false}));
    assert.equal(no_device.ok, false);
    assert.match(no_device.text, /No phone is registered/);

    const failed = notification_check.phone_status(
        push_check({sent: false, error: "Network error."}),
    );
    assert.equal(failed.ok, false);
    assert.match(failed.text, /could not be sent\. Network error\./);

    const sent = notification_check.phone_status(push_check());
    assert.equal(sent.ok, true);
});

run_test("the check shows a test notification and asks the server for the phone", async () => {
    permission = "granted";
    is_mobile = false;
    shown.length = 0;
    next_response = push_check();
    const [browser, phone] = await notification_check.run_check();
    assert.equal(posted.url, "/json/mobile_push/check");
    assert.equal(shown.length, 1);
    assert.equal(requested, 0);
    assert.equal(browser.ok, true);
    assert.equal(phone.ok, true);
});

run_test("the check asks the browser first when it has not been asked", async () => {
    permission = "default";
    requested = 0;
    shown.length = 0;
    next_response = push_check({device_count: 0, sent: false});
    const [browser, phone] = await notification_check.run_check();
    assert.equal(requested, 1);
    assert.equal(shown.length, 1);
    assert.equal(browser.ok, true);
    assert.equal(phone.ok, false);
});

run_test("a blocked browser shows nothing and is reported", async () => {
    permission = "denied";
    requested = 0;
    shown.length = 0;
    next_response = push_check();
    const [browser, phone] = await notification_check.run_check();
    assert.equal(requested, 0);
    assert.equal(shown.length, 0);
    assert.equal(browser.ok, false);
    assert.equal(phone.ok, true);
    assert.ok(desktop_notifications);
});

run_test("a reply that does not match the schema is an error", async () => {
    permission = "granted";
    next_response = {push_service_configured: true};
    await assert.rejects(notification_check.run_check());
});
