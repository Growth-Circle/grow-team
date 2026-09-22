import _ from "lodash";

import render_more_private_conversations from "../templates/more_pms.hbs";
import render_pm_list_item from "../templates/pm_list_item.hbs";

import * as people from "./people.ts";
import type {DisplayObject} from "./pm_list_data.ts";
import * as vdom from "./vdom.ts";

export type PMNode =
    | {
          type: "conversation";
          conversation: DisplayObject;
      }
    | {
          type: "more_items";
          more_conversations_unread_count: number;
      };

// Number of identity-avatar colors a 1:1 DM partner can be assigned
// (frame 10a). Must match the length of people.ts's private
// DEFAULT_AVATAR_PALETTE; see the duplication note on
// dm_avatar_color_index below.
const DM_AVATAR_COLOR_COUNT = 5;

// Two-letter initials for a DM list avatar (frame 10a). This mirrors
// people.ts's private initials_for_full_name, which this file cannot
// import because this change is scoped to leave people.ts untouched.
//
// ponytail: export the people.ts version instead, once a change is
// scoped to touch both files, to remove this duplicate.
function dm_avatar_initials(full_name: string): string {
    const words = full_name.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) {
        return "";
    }
    if (words.length === 1) {
        return words[0]!.slice(0, 2).toUpperCase();
    }
    return (words[0]![0]! + words.at(-1)![0]!).toUpperCase();
}

// Picks one of left_sidebar.css's .dm-avatar-color-0..4 classes by
// user_id, so a given person's avatar color stays stable. Mirrors
// people.ts's private default_avatar_fill bucketing.
function dm_avatar_color_index(user_id: number): number {
    return user_id % DM_AVATAR_COLOR_COUNT;
}

// Adds the initials-avatar fields pm_list_item.hbs needs to render a
// 1:1 conversation's avatar (frame 10a). Group conversations keep
// their existing group icon and don't need these fields.
function with_avatar_fields(conversation: DisplayObject): unknown {
    if (conversation.is_group) {
        return conversation;
    }

    const user_id = Number.parseInt(conversation.user_ids_string, 10);
    return {
        ...conversation,
        avatar_initials: dm_avatar_initials(people.get_full_name(user_id)),
        avatar_color_index: dm_avatar_color_index(user_id),
    };
}

export function keyed_pm_li(conversation: DisplayObject): vdom.Node<PMNode> {
    const render = (): string => render_pm_list_item(with_avatar_fields(conversation));

    const eq = (other: PMNode): boolean =>
        other.type === "conversation" && _.isEqual(conversation, other.conversation);

    const key = conversation.user_ids_string;

    return {
        key,
        render,
        eq,
        type: "conversation",
        conversation,
    };
}

export function more_private_conversations_li(
    more_conversations_unread_count: number,
): vdom.Node<PMNode> {
    const render = (): string =>
        render_more_private_conversations({more_conversations_unread_count});

    // Used in vdom.js to check if an element has changed and needs to
    // be updated in the DOM.
    const eq = (other: PMNode): boolean =>
        other.type === "more_items" &&
        more_conversations_unread_count === other.more_conversations_unread_count;

    // This special key must be impossible as a user_ids_string.
    const key = "more_private_conversations";

    return {
        key,
        render,
        eq,
        type: "more_items",
        more_conversations_unread_count,
    };
}

export function pm_ul(nodes: vdom.Node<PMNode>[]): vdom.Tag<PMNode> {
    const attrs: [string, string][] = [
        ["class", "dm-list"],
        ["data-name", "private"],
    ];
    return vdom.ul({
        attrs,
        keyed_nodes: nodes,
    });
}
