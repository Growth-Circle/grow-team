/* eslint-disable no-bitwise -- UUID version and variant bits must be set on random bytes. */
export type DetailResponseFence = {
    active: boolean;
    request: number;
    accepted_request: number;
    requested_operation_offset: number;
    requested_artifact_offset: number;
    current_operation_offset: number;
    current_artifact_offset: number;
    incoming_version: number;
    current_version: number | undefined;
};

export function new_client_key(): string {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6]! & 0x0f) | 0x40;
    bytes[8] = (bytes[8]! & 0x3f) | 0x80;
    const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function accepts_detail_response(fence: DetailResponseFence): boolean {
    return (
        fence.active &&
        fence.request >= fence.accepted_request &&
        fence.requested_operation_offset === fence.current_operation_offset &&
        fence.requested_artifact_offset === fence.current_artifact_offset &&
        (fence.current_version === undefined || fence.incoming_version >= fence.current_version)
    );
}

export function accepts_auxiliary_response(args: {
    active: boolean;
    request: number;
    latest_request: number;
    attempt_id: string | undefined;
    current_attempt_id: string | undefined;
    requested_job_version: number | undefined;
    current_job_version: number | undefined;
}): boolean {
    return (
        args.active &&
        args.request === args.latest_request &&
        args.attempt_id === args.current_attempt_id &&
        args.requested_job_version === args.current_job_version
    );
}

export function merge_event_sequences<T extends {sequence: number}>(
    existing: T[],
    incoming: T[],
): T[] {
    return [...new Map([...existing, ...incoming].map((item) => [item.sequence, item])).values()]
        .toSorted((left, right) => left.sequence - right.sequence)
        .slice(-1000);
}

export type InputIntent = {
    job_id: string;
    text: string;
    key: string;
    expected_version: number;
    draft_revision: number;
};

export function input_intent_for_draft(
    current: InputIntent | undefined,
    job_id: string,
    text: string,
    expected_version: number,
    draft_revision: number,
    new_key: () => string,
): InputIntent {
    if (
        current?.job_id === job_id &&
        current.text === text &&
        current.draft_revision === draft_revision
    ) {
        return current;
    }
    return {job_id, text, key: new_key(), expected_version, draft_revision};
}

export function clears_input_on_ack(intent: InputIntent, current_revision: number): boolean {
    return intent.draft_revision === current_revision;
}
