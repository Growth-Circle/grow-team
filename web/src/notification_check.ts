import * as z from "zod/mini";

import * as channel from "./channel.ts";
import * as desktop_notifications from "./desktop_notifications.ts";
import {$t} from "./i18n.ts";
import * as message_notifications from "./message_notifications.ts";
import * as util from "./util.ts";

// The "Send a test notification" check of the notification settings: a
// test on each way a reply can reach the person, and what is not set up.
// The browser is checked here. The phone is checked by the server, which
// alone knows the push service and the registered devices.

const push_check_schema = z.object({
    push_service_configured: z.boolean(),
    device_count: z.number(),
    sent: z.boolean(),
    error: z.nullable(z.string()),
});

export type PushCheck = z.infer<typeof push_check_schema>;

export type ChannelStatus = {
    name: string;
    ok: boolean;
    state: string;
    text: string;
};

function status(name: string, ok: boolean, text: string): ChannelStatus {
    return {
        name,
        ok,
        state: ok ? $t({defaultMessage: "Working"}) : $t({defaultMessage: "Not set up"}),
        text,
    };
}

export function browser_status(permission: string, is_mobile: boolean): ChannelStatus {
    const name = $t({defaultMessage: "Browser"});
    if (is_mobile) {
        return status(
            name,
            false,
            $t({defaultMessage: "A phone browser cannot show notifications. Use the Zulip app."}),
        );
    }
    if (permission === "granted") {
        return status(
            name,
            true,
            $t({defaultMessage: "A test notification was shown on this computer."}),
        );
    }
    if (permission === "denied") {
        return status(
            name,
            false,
            $t({
                defaultMessage:
                    "Blocked. Allow notifications for this site in your browser's site settings, then reload the page.",
            }),
        );
    }
    return status(
        name,
        false,
        $t({
            defaultMessage:
                "Not turned on. Choose Allow when your browser asks, then send the test again.",
        }),
    );
}

export function phone_status(check: PushCheck): ChannelStatus {
    const name = $t({defaultMessage: "Phone"});
    if (!check.push_service_configured) {
        return status(
            name,
            false,
            $t({
                defaultMessage:
                    "This server has no push service set up. Ask an administrator to set it up.",
            }),
        );
    }
    if (check.device_count === 0) {
        return status(
            name,
            false,
            $t({
                defaultMessage:
                    "No phone is registered. Install the Zulip app and log in to this workspace.",
            }),
        );
    }
    if (!check.sent) {
        return status(
            name,
            false,
            $t(
                {defaultMessage: "The test push could not be sent. {reason}"},
                {reason: check.error ?? ""},
            ),
        );
    }
    return status(
        name,
        true,
        $t({
            defaultMessage: "A test push was sent to your phone. It should arrive within a minute.",
        }),
    );
}

export async function run_check(): Promise<ChannelStatus[]> {
    const is_mobile = util.is_mobile();
    let permission = desktop_notifications.permission_state();
    if (!is_mobile && permission === "default") {
        // The click that started the check is the gesture the browser wants.
        permission = await desktop_notifications.request_desktop_notifications_permission();
    }
    if (!is_mobile && permission === "granted") {
        message_notifications.send_test_notification(
            $t({defaultMessage: "This is a test notification from sanji."}),
        );
    }
    const result: unknown = await channel.post({url: "/json/mobile_push/check"});
    return [browser_status(permission, is_mobile), phone_status(push_check_schema.parse(result))];
}
