import {execFileSync} from "node:child_process";
import {randomUUID} from "node:crypto";
import {setTimeout as sleep} from "node:timers/promises";
import type {Data} from "./protocol.js";
import type {JournalLog} from "./journal.js";
import type {AttemptChannel} from "./supervisor.js";

export interface GitPushRequest {
    remote: string;
    branch: string;
    commit: string;
    expected_remote_head: string | null;
    credential: string | null;
    git_dir?: string;
}

const gitEnvironment = (credential: string | null): NodeJS.ProcessEnv => {
    const env: NodeJS.ProcessEnv = {
        PATH: "/usr/bin:/bin",
        HOME: "/nonexistent",
        GIT_CONFIG_GLOBAL: "/dev/null",
        GIT_CONFIG_NOSYSTEM: "1",
        GIT_TERMINAL_PROMPT: "0",
        GIT_NO_REPLACE_OBJECTS: "1",
        GIT_NO_LAZY_FETCH: "1",
    };
    if (credential) {
        env.GIT_CONFIG_COUNT = "1";
        env.GIT_CONFIG_KEY_0 = "http.extraHeader";
        env.GIT_CONFIG_VALUE_0 = `Authorization: Basic ${Buffer.from(`x-access-token:${credential}`).toString("base64")}`;
    }
    return env;
};

function sha(value: string | null): boolean {
    return value === null || /^[0-9a-f]{40}$/.test(value);
}

function ref(branch: string): string {
    if (
        !/^grow-agent\/[a-zA-Z0-9_-]{1,80}\/[a-zA-Z0-9_-]{1,80}$/.test(branch) ||
        branch.startsWith("-")
    )
        throw new Error("Unsafe remote branch");
    return `refs/heads/${branch}`;
}

function remote(value: string): string {
    if (!value || value.length > 4096 || /[\s\x00-\x1f]/.test(value) || value.startsWith("-"))
        throw new Error("Unsafe remote");
    return value;
}

export class RemoteGitBroker {
    private git(args: string[], credential: string | null): string {
        try {
            return execFileSync(
                "/usr/bin/git",
                [
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.attributesFile=/dev/null",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "diff.external=",
                    "-c",
                    "protocol.allow=never",
                    "-c",
                    "protocol.file.allow=always",
                    "-c",
                    "protocol.https.allow=always",
                    "-c",
                    "protocol.ssh.allow=always",
                    ...args,
                ],
                {
                    env: gitEnvironment(credential),
                    encoding: "utf8",
                    timeout: 20000,
                    maxBuffer: 1024 * 1024,
                },
            ).trim();
        } catch {
            throw new Error("Remote Git operation failed");
        }
    }
    async head(
        request: Pick<GitPushRequest, "remote" | "branch" | "credential">,
    ): Promise<string | null> {
        const target = ref(request.branch);
        const result = this.git(
            ["ls-remote", "--refs", remote(request.remote), target],
            request.credential,
        );
        if (!result) return null;
        const rows = result.split("\n");
        if (rows.length !== 1) throw new Error("Remote ref result is ambiguous");
        const match = /^([0-9a-f]{40})\t(.+)$/.exec(rows[0]!);
        if (!match || match[2] !== target) throw new Error("Remote ref result is invalid");
        return match[1]!;
    }
    private assertAncestry(request: GitPushRequest): void {
        if (!request.expected_remote_head) return;
        if (!request.git_dir) throw new Error("Candidate ancestry metadata is unavailable");
        try {
            execFileSync(
                "/usr/bin/git",
                [
                    `--git-dir=${request.git_dir}`,
                    "merge-base",
                    "--is-ancestor",
                    request.expected_remote_head,
                    request.commit,
                ],
                {env: gitEnvironment(null), timeout: 10000, stdio: "ignore"},
            );
        } catch {
            throw new Error("Approved remote head is not a candidate ancestor");
        }
    }
    async importExpectedHead(args: {
        remote: string;
        branch: string;
        expected_remote_head: string;
        credential: string | null;
        git_dir: string;
        base_commit: string;
    }): Promise<void> {
        if (
            !/^[0-9a-f]{40}$/.test(args.expected_remote_head) ||
            !/^[0-9a-f]{40}$/.test(args.base_commit) ||
            !args.git_dir
        )
            throw new Error("Invalid approved remote parent");
        const target = ref(args.branch);
        const local = `refs/grow-agent/approved/${args.expected_remote_head}`;
        this.git(
            [
                `--git-dir=${args.git_dir}`,
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                remote(args.remote),
                `${target}:${local}`,
            ],
            args.credential,
        );
        if (
            this.git(
                [`--git-dir=${args.git_dir}`, "rev-parse", "--verify", `${local}^{commit}`],
                null,
            ) !== args.expected_remote_head
        )
            throw new Error("Approved remote parent changed");
        try {
            execFileSync(
                "/usr/bin/git",
                [
                    `--git-dir=${args.git_dir}`,
                    "merge-base",
                    "--is-ancestor",
                    args.base_commit,
                    args.expected_remote_head,
                ],
                {env: gitEnvironment(null), timeout: 10000, stdio: "ignore"},
            );
        } catch {
            throw new Error("Approved remote head is outside the candidate lineage");
        }
    }
    async push(request: GitPushRequest): Promise<{remote_head: string}> {
        if (!/^[0-9a-f]{40}$/.test(request.commit) || !sha(request.expected_remote_head))
            throw new Error("Invalid Git commit identity");
        if (!request.git_dir) throw new Error("Candidate Git metadata is unavailable");
        const target = ref(request.branch),
            current = await this.head(request);
        if (current !== request.expected_remote_head)
            throw new Error("Expected remote head changed");
        this.assertAncestry(request);
        const lease = `${target}:${request.expected_remote_head ?? ""}`;
        this.git(
            [
                `--git-dir=${request.git_dir}`,
                "push",
                "--porcelain",
                "--no-verify",
                `--force-with-lease=${lease}`,
                remote(request.remote),
                `${request.commit}:${target}`,
            ],
            request.credential,
        );
        const observed = await this.head(request);
        if (observed !== request.commit) throw new Error("Remote Git receipt is not verified");
        return {remote_head: observed};
    }
}

export interface GitHubConfiguration {
    api_base: string;
    credential: string;
}
export interface RemotePublicationConfiguration {
    remote: string;
    git_credential: string | null;
    github: GitHubConfiguration | null;
}
interface CandidateBroker {
    candidateCommit(parent?: string): Promise<{
        tree: string;
        commit: string;
        git_dir: string;
        base_commit: string;
    }>;
}
function githubRepository(remoteValue: string): {owner: string; repository: string; host: string} {
    const match =
        /^https:\/\/([a-zA-Z0-9.-]+)\/([a-zA-Z0-9_.-]+)\/([a-zA-Z0-9_.-]+?)(?:\.git)?$/.exec(
            remoteValue,
        );
    if (!match) throw new Error("GitHub publication requires an HTTPS repository origin");
    return {host: match[1]!, owner: match[2]!, repository: match[3]!};
}
function apiBase(value: string): string {
    const url = new URL(value);
    if (
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        (url.protocol !== "https:" &&
            !(
                url.protocol === "http:" &&
                ["127.0.0.1", "[::1]", "localhost"].includes(url.hostname)
            ))
    )
        throw new Error("Unsafe GitHub API endpoint");
    return url.toString().replace(/\/$/, "");
}
export class GitHubDraftProvider {
    constructor(private configuration: GitHubConfiguration) {}
    private async request(
        path: string,
        init: RequestInit,
    ): Promise<{data: Data; headers: Headers}> {
        const abort = new AbortController(),
            timer = setTimeout(() => abort.abort(), 15000);
        try {
            const response = await fetch(`${apiBase(this.configuration.api_base)}${path}`, {
                ...init,
                redirect: "manual",
                signal: abort.signal,
                headers: {
                    Accept: "application/vnd.github+json",
                    Authorization: `Bearer ${this.configuration.credential}`,
                    "X-GitHub-Api-Version": "2026-03-10",
                    ...(init.headers ?? {}),
                },
            });
            if (!response.ok) throw new Error("GitHub draft request failed");
            const text = await response.text();
            if (Buffer.byteLength(text) > 1024 * 1024)
                throw new Error("GitHub response exceeds limit");
            const data = JSON.parse(text);
            if (!data || typeof data !== "object") throw new Error("Invalid GitHub response");
            return {data: data as Data, headers: response.headers};
        } finally {
            clearTimeout(timer);
        }
    }
    private valid(item: Data, expected: Data, host: string): {id: string; url: string} | null {
        let url: URL;
        try {
            url = new URL(item.html_url as string);
        } catch {
            return null;
        }
        if (
            url.protocol !== "https:" ||
            url.username ||
            url.password ||
            url.hostname !== host ||
            item.title !== expected.title ||
            item.body !== expected.body ||
            item.draft !== true ||
            item.base?.ref !== expected.base ||
            item.head?.ref !== expected.head ||
            item.head?.sha !== expected.commit ||
            (typeof item.id !== "number" && typeof item.id !== "string")
        )
            return null;
        return {id: String(item.id), url: url.toString()};
    }
    async find(remoteValue: string, expected: Data): Promise<{id: string; url: string} | null> {
        const repo = githubRepository(remoteValue);
        const query = new URLSearchParams({
            state: "all",
            base: expected.base,
            head: `${repo.owner}:${expected.head}`,
            per_page: "100",
        });
        const response = await this.request(
            `/repos/${encodeURIComponent(repo.owner)}/${encodeURIComponent(repo.repository)}/pulls?${query}`,
            {method: "GET"},
        );
        if (!Array.isArray(response.data) || response.headers.get("link"))
            throw new Error("GitHub draft reconciliation is incomplete");
        const matches = response.data
            .map((item) =>
                item && typeof item === "object" && !Array.isArray(item)
                    ? this.valid(item as Data, expected, repo.host)
                    : null,
            )
            .filter((item): item is {id: string; url: string} => item !== null);
        if (matches.length > 1) throw new Error("GitHub draft reconciliation is ambiguous");
        return matches[0] ?? null;
    }
    async create(remoteValue: string, expected: Data): Promise<{id: string; url: string}> {
        const repo = githubRepository(remoteValue);
        try {
            const response = await this.request(
                `/repos/${encodeURIComponent(repo.owner)}/${encodeURIComponent(repo.repository)}/pulls`,
                {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        title: expected.title,
                        body: expected.body,
                        head: expected.head,
                        base: expected.base,
                        draft: true,
                    }),
                },
            );
            const result = this.valid(response.data, expected, repo.host);
            if (!result) throw new Error("GitHub draft response differs from approval");
            return result;
        } catch {
            const found = await this.find(remoteValue, expected);
            if (found) return found;
            throw new Error("GitHub draft outcome is uncertain");
        }
    }
}
export class RemoteOperationBroker {
    constructor(
        private configuration: (descriptor: Data) => RemotePublicationConfiguration | null,
        private git = new RemoteGitBroker(),
        private journal?: JournalLog,
        private recoveryConfiguration: (
            remote: string,
        ) => RemotePublicationConfiguration | null = () => null,
    ) {}
    private async currentPublication(channel: AttemptChannel): Promise<boolean> {
        await channel.pollInputs?.();
        if (channel.hasPendingInput?.()) return false;
        channel.lease();
        return true;
    }
    private async approve(
        channel: AttemptChannel,
        id: string,
        args: Data,
        extras: Data,
    ): Promise<Data | null> {
        if (!(await this.currentPublication(channel))) return null;
        let proposal = await channel.operations.propose(channel.lease(), id, args, extras);
        while (proposal.status === "proposed") {
            if (!(await this.currentPublication(channel))) return null;
            const controls = await channel.request?.("/runner/controls");
            const control = controls?.controls?.find(
                (item: Data) =>
                    item.attempt_id === channel.lease().attempt_id &&
                    item.lease_epoch === channel.lease().lease_epoch,
            );
            const approval = control?.approvals?.find(
                (item: Data) => item.operation_id === id && item.nonce === proposal.nonce,
            );
            if (approval?.decision === "approved") break;
            if (approval && approval.decision !== "pending")
                throw new Error("Remote operation approval was rejected");
            await sleep(500);
            if (!(await this.currentPublication(channel))) return null;
            const current = (await channel.operations.reconcile(channel.lease())).operations.find(
                (item: Data) => item.operation_id === id,
            );
            if (!current || current.operation_hash !== proposal.operation_hash)
                throw new Error("Remote operation approval changed");
            proposal = current;
        }
        if (!(await this.currentPublication(channel))) return null;
        const operation = await channel.operations.consume(channel.lease(), proposal);
        channel.operations.beginEffect(id);
        await channel.event("tool.started", {
            operation_id: id,
            tool_class: args.action,
            argument_digest: operation.operation_hash,
            status: "started",
            artifact_id: null,
            exit_code: null,
            summary: "",
        });
        return operation;
    }
    private async receipt(channel: AttemptChannel, operation: Data, receipt: Data): Promise<void> {
        await channel.operations.remoteReceipt(channel.lease(), receipt);
        channel.operations.finishEffect(receipt.operation_id, receipt);
        await channel.event("tool.finished", {
            operation_id: receipt.operation_id,
            tool_class: operation.tool_class,
            argument_digest: operation.operation_hash,
            status: "succeeded",
            artifact_id: null,
            exit_code: 0,
            summary: "",
        });
    }
    async publish(
        descriptor: Data,
        broker: CandidateBroker,
        result: Data,
        channel: AttemptChannel,
    ): Promise<boolean> {
        if (descriptor.delivery_target !== "draft_pr") return true;
        const config = this.configuration(descriptor);
        if (!config || !config.github || config.remote !== descriptor.repository?.canonical_origin)
            throw new Error("Owner remote publication is not configured");
        if (!result.verification?.passed || typeof result.verification.diff?.id !== "string")
            throw new Error("Final tree verification is incomplete");
        if (!(await this.currentPublication(channel))) return false;
        const preliminary = await broker.candidateCommit(),
            branch = `grow-agent/${descriptor.job_id}/${descriptor.attempt_id}`;
        const expected = await this.git.head({
            remote: config.remote,
            branch,
            credential: config.git_credential,
        });
        if (!(await this.currentPublication(channel))) return false;
        let candidate = preliminary;
        if (expected) {
            await this.git.importExpectedHead({
                remote: config.remote,
                branch,
                expected_remote_head: expected,
                credential: config.git_credential,
                git_dir: preliminary.git_dir,
                base_commit: preliminary.base_commit,
            });
            if (!(await this.currentPublication(channel))) return false;
            candidate = await broker.candidateCommit(expected);
        }
        const pushId = randomUUID();
        const push = await this.approve(
            channel,
            pushId,
            {
                action: "git.push",
                repository_id: descriptor.repository.id,
                remote: config.remote,
                branch,
                commit: candidate.commit,
                expected_remote_head: expected,
                force: false,
            },
            {tree_hash: candidate.tree, diff_artifact_id: result.verification.diff.id},
        );
        if (!push) return false;
        if (!(await this.currentPublication(channel))) return false;
        await this.git.push({
            remote: config.remote,
            branch,
            commit: candidate.commit,
            expected_remote_head: expected,
            credential: config.git_credential,
            git_dir: candidate.git_dir,
        });
        await this.receipt(channel, push, {
            remote: config.remote,
            branch,
            commit: candidate.commit,
            operation_id: pushId,
            pull_request_id: null,
            pull_request_url: null,
            observed_at: new Date().toISOString(),
        });
        const prId = randomUUID(),
            title = `Grow Agent task ${descriptor.job_id}`,
            body = `${descriptor.request}\n\n<!-- grow-agent-operation:${prId} -->`;
        if (Buffer.byteLength(body) > 20000)
            throw new Error("Draft pull request body exceeds approval limit");
        const args = {
            action: "git.draft_pr",
            repository_id: descriptor.repository.id,
            remote: config.remote,
            base: descriptor.base_ref,
            head: branch,
            commit: candidate.commit,
            title,
            body,
            draft: true,
        };
        const pr = await this.approve(channel, prId, args, {
            tree_hash: candidate.tree,
            diff_artifact_id: result.verification.diff.id,
        });
        if (!pr) return false;
        if (!(await this.currentPublication(channel))) return false;
        const created = await new GitHubDraftProvider(config.github).create(config.remote, args);
        await this.receipt(channel, pr, {
            remote: config.remote,
            branch,
            commit: candidate.commit,
            operation_id: prId,
            pull_request_id: created.id,
            pull_request_url: created.url,
            observed_at: new Date().toISOString(),
        });
        return true;
    }
    async recover(
        operationRecovery: (attemptId: string) => {remoteReceipt(receipt: Data): Promise<Data>},
    ): Promise<void> {
        if (!this.journal) return;
        for (const effect of this.journal.list("effect")) {
            if (effect.state !== "uncertain") continue;
            const operation = effect.request.operation;
            if (!operation || !["git.push", "git.draft_pr"].includes(operation.tool_class))
                continue;
            const args = operation.arguments as Data;
            const config = this.recoveryConfiguration(args.remote);
            if (!config) continue;
            let receipt: Data | null = null;
            if (operation.tool_class === "git.push") {
                const head = await this.git.head({
                    remote: args.remote,
                    branch: args.branch,
                    credential: config.git_credential,
                });
                if (head === args.commit)
                    receipt = {
                        remote: args.remote,
                        branch: args.branch,
                        commit: args.commit,
                        operation_id: operation.operation_id,
                        pull_request_id: null,
                        pull_request_url: null,
                        observed_at: new Date().toISOString(),
                    };
            } else if (config.github) {
                const found = await new GitHubDraftProvider(config.github).find(args.remote, args);
                if (found)
                    receipt = {
                        remote: args.remote,
                        branch: args.head,
                        commit: args.commit,
                        operation_id: operation.operation_id,
                        pull_request_id: found.id,
                        pull_request_url: found.url,
                        observed_at: new Date().toISOString(),
                    };
            }
            const authority = this.journal.get(`authority:${operation.operation_id}`);
            const attemptId = authority?.request.lease?.attempt_id;
            if (receipt && typeof attemptId === "string")
                await operationRecovery(attemptId).remoteReceipt(receipt);
        }
    }
}
