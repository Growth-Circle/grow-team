"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const {
    action_button_label,
    auth_state_label,
    data_scope_label,
    default_mode_label,
    desired_state_label,
    grant_action_label,
    host_kind_label,
    model_location_label,
    presence_label,
    readiness_label,
    requirement_sentence,
    setup_phase_label,
    sharing_label,
    team_default_badge_label,
} = zrequire("agent_settings_labels");

// The test harness's $t() stub returns "translated: " plus the English
// source text, so every expected string below carries that same prefix.
function t(text) {
    return `translated: ${text}`;
}

run_test("desired state labels cover every value and fall back for an unknown one", () => {
    assert.equal(desired_state_label("draft"), t("Draft (not taking tasks)"));
    assert.equal(desired_state_label("enabled"), t("On"));
    assert.equal(desired_state_label("paused"), t("Paused"));
    assert.equal(desired_state_label("archived"), t("Archived"));
    assert.equal(desired_state_label("something_new"), t("Not available"));
});

run_test("readiness labels cover every value and fall back for an unknown one", () => {
    assert.equal(readiness_label("unchecked"), t("Not checked"));
    assert.equal(readiness_label("checking"), t("Checking"));
    assert.equal(readiness_label("ready"), t("Ready"));
    assert.equal(readiness_label("needs_action"), t("Needs a fix"));
    assert.equal(readiness_label("error"), t("Check failed"));
    assert.equal(readiness_label("something_new"), t("Not available"));
});

run_test("presence labels cover every value and fall back for an unknown one", () => {
    assert.equal(presence_label("online"), t("Connected"));
    assert.equal(presence_label("offline"), t("Offline"));
    assert.equal(presence_label("unknown"), t("Status unknown"));
    assert.equal(presence_label("revoked"), t("Removed"));
    assert.equal(presence_label("something_new"), t("Not available"));
});

run_test("host kind labels cover every value and fall back for an unknown one", () => {
    assert.equal(host_kind_label("workstation"), t("Personal computer"));
    assert.equal(host_kind_label("server"), t("Server"));
    assert.equal(host_kind_label("unknown"), t("Device type not set"));
    assert.equal(host_kind_label("something_new"), t("Not available"));
});

run_test("setup phase labels cover every value and fall back for an unknown one", () => {
    assert.equal(setup_phase_label("pending"), t("Waiting for the device"));
    assert.equal(setup_phase_label("probing"), t("Checking"));
    assert.equal(setup_phase_label("ready"), t("Check passed"));
    assert.equal(setup_phase_label("needs_action"), t("Needs a fix"));
    assert.equal(setup_phase_label("failed"), t("Check failed"));
    assert.equal(setup_phase_label("cancelled"), t("Check replaced"));
    assert.equal(setup_phase_label("something_new"), t("Not available"));
});

run_test("auth state labels cover every value and fall back for an unknown one", () => {
    assert.equal(auth_state_label("unchecked"), t("Sign-in not checked"));
    assert.equal(auth_state_label("ready"), t("Signed in"));
    assert.equal(auth_state_label("login_required"), t("Sign-in needed"));
    assert.equal(auth_state_label("expired"), t("Sign-in expired"));
    assert.equal(auth_state_label("error"), t("Sign-in check failed"));
    assert.equal(auth_state_label("something_new"), t("Not available"));
});

run_test("default mode labels cover every value and fall back for an unknown one", () => {
    assert.equal(default_mode_label("answer"), t("Answer"));
    assert.equal(default_mode_label("code"), t("Coding"));
    assert.equal(default_mode_label("manage"), t("Team management"));
    assert.equal(default_mode_label("something_new"), t("Not available"));
});

run_test("model location labels cover every value and fall back for an unknown one", () => {
    assert.equal(model_location_label("runner_local"), t("Model: on the device"));
    assert.equal(model_location_label("private_network"), t("Model: private network"));
    assert.equal(model_location_label("external"), t("Model: external service"));
    assert.equal(model_location_label("something_new"), t("Not available"));
});

run_test("data scope labels cover every value and fall back for an unknown one", () => {
    assert.equal(data_scope_label("synthetic"), t("Test data"));
    assert.equal(data_scope_label("selected_chat"), t("Selected chat messages"));
    assert.equal(data_scope_label("selected_repository"), t("Selected repository files"));
    assert.equal(data_scope_label("something_new"), t("Not available"));
});

run_test("sharing label has exactly three variants", () => {
    assert.equal(sharing_label(true, 0), t("Private to you"));
    assert.equal(sharing_label(true, 1), t("Shared by you"));
    assert.equal(sharing_label(true, 5), t("Shared by you"));
    assert.equal(sharing_label(false, 0), t("Shared with you"));
    assert.equal(sharing_label(false, 3), t("Shared with you"));
});

run_test("the team default badge label is fixed", () => {
    assert.equal(team_default_badge_label(), t("Team default"));
});

run_test("action button labels cover every value and fall back for an unknown one", () => {
    assert.equal(action_button_label("edit"), t("Edit"));
    assert.equal(action_button_label("probe"), t("Run check"));
    assert.equal(action_button_label("enable"), t("Turn on"));
    assert.equal(action_button_label("pause"), t("Pause"));
    assert.equal(action_button_label("archive"), t("Archive"));
    assert.equal(action_button_label("test_task"), t("Send test task"));
    assert.equal(action_button_label("something_new"), t("Not available"));
});

run_test("grant action labels cover every value and fall back for an unknown one", () => {
    assert.equal(grant_action_label("profile.use"), t("Use this agent"));
    assert.equal(grant_action_label("context.read"), t("Read the task conversation"));
    assert.equal(grant_action_label("repository.read"), t("Read the repository"));
    assert.equal(grant_action_label("repository.edit"), t("Edit files in a task copy"));
    assert.equal(grant_action_label("checks.run"), t("Run required checks"));
    assert.equal(grant_action_label("shell.run"), t("Run commands"));
    assert.equal(grant_action_label("dependencies.install"), t("Install dependencies"));
    assert.equal(grant_action_label("git.commit"), t("Make local commits"));
    assert.equal(grant_action_label("git.push"), t("Push task branches"));
    assert.equal(grant_action_label("git.draft_pr"), t("Open draft pull requests"));
    assert.equal(grant_action_label("job.control"), t("Stop and resume tasks"));
    assert.equal(grant_action_label("job.review"), t("Review results"));
    assert.equal(grant_action_label("provider.use"), t("Use the model connection"));
    assert.equal(grant_action_label("runner.use"), t("Use the device"));
    assert.equal(grant_action_label("profile.manage"), t("Manage this agent"));
    assert.equal(grant_action_label("team.manage"), t("Give team management tasks"));
    assert.equal(grant_action_label("something_new"), t("Not available"));
});

run_test("requirement sentences cover every code and fall back to the generic one", () => {
    assert.equal(
        requirement_sentence("runtime_missing"),
        t(
            "The agent program is not installed on the device. Install it on the device, then run the check again.",
        ),
    );
    assert.equal(
        requirement_sentence("runtime_unsupported"),
        t(
            "This agent program version is not supported. Install a supported version on the device, then run the check again.",
        ),
    );
    assert.equal(
        requirement_sentence("auth_required"),
        t(
            "The agent program must sign in on the device. Sign in on the device, then run the check again.",
        ),
    );
    assert.equal(
        requirement_sentence("auth_unknown"),
        t(
            "The device did not report whether the agent program is signed in. Sign in on the device, then run the check again.",
        ),
    );
    assert.equal(
        requirement_sentence("sandbox_unavailable"),
        t(
            "The device has not approved the work environment for this agent. Approve it on the device, then run the check again.",
        ),
    );
    assert.equal(
        requirement_sentence("controlled_provider_required"),
        t("Choose a model connection for this agent."),
    );
    assert.equal(
        requirement_sentence("probe_incomplete"),
        t(
            "The check did not finish. Run the check again. If it fails again, ask the device owner.",
        ),
    );
    assert.equal(
        requirement_sentence("profile_needs_action"),
        t("This agent needs a new check. Run the check again."),
    );
    assert.equal(
        requirement_sentence("runner_offline"),
        t("The device is not connected. Start the runner on the device."),
    );
    const generic = t("This agent needs a fix before it can run. Ask its owner.");
    assert.equal(requirement_sentence("other"), generic);
    assert.equal(requirement_sentence("something_new"), generic);
});
