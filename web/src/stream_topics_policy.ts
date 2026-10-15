// The "Chat without topics" option in the channel creation form is a
// shortcut for the channel's `topics_policy` setting: on, it is
// `empty_topic_only`; off, the channel follows the organization default.
// The setting itself stays under Advanced configuration, and the two
// controls follow each other.

const NO_TOPICS_POLICY = "empty_topic_only";
const DEFAULT_TOPICS_POLICY = "inherit";

export function policy_for_no_topics_option(is_checked: boolean): string {
    return is_checked ? NO_TOPICS_POLICY : DEFAULT_TOPICS_POLICY;
}

export function is_no_topics_policy(policy: string): boolean {
    return policy === NO_TOPICS_POLICY;
}
