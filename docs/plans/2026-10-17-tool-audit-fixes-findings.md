# Tool audit fixes: findings

## Baseline (before change)

Measured 2026-10-17 by Task 1 before any source change. Logs are verbatim from /private/tmp/claude-502/taf.

### Step 2: Codex self-reports (exit codes: all probe=0)

banned_lines: codex-grader=1, codex-fast=1, codex-hook=1, codex-broker=1

#### b-codex-grader.log
```
argv=["codex", "exec", "-c", "model_reasoning_effort=\"low\"", "-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", "features.view_image=false", "-c", "features.apps=false", "-c", "features.browser_use=false", "-c", "features.computer_use=false", "-c", "features.multi_agent=false", "-c", "features.image_generation=false", "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--output-schema", "/private/tmp/claude-502/taf/tmpz7tqxhg7.json", "-s", "read-only", "-m", "gpt-5.6-sol", "-"]
rc=0
answer="functions.wait, functions.request_user_input, functions.exec, collaboration.followup_task, collaboration.interrupt_agent, collaboration.list_agents, collaboration.send_message, collaboration.spawn_agent, collaboration.wait_agent"
tool_items=[]
stderr_tail=""
```

#### b-codex-fast.log
```
argv=["codex", "exec", "-c", "model_reasoning_effort=\"low\"", "-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", "features.view_image=false", "-c", "features.apps=false", "-c", "features.browser_use=false", "-c", "features.computer_use=false", "-c", "features.multi_agent=false", "-c", "features.image_generation=false", "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--output-schema", "/private/tmp/claude-502/taf/tmp5a9wx9py.json", "-s", "read-only", "-m", "gpt-5.6-luna", "-"]
rc=0
answer="functions.wait, functions.request_user_input, functions.exec, functions.apply_patch, functions.create_goal, functions.get_goal, functions.list_mcp_resource_templates, functions.list_mcp_resources, functions.read_mcp_resource, functions.update_goal, functions.web__run"
tool_items=[]
stderr_tail=""
```

#### b-codex-hook.log
```
argv=["bash", "-c", "<plan-validator.sh _call_fast_model>", "codex"]
hook_stdout="hook_model=gpt-6-luna\n{\"answer\":\"functions.wait, functions.request_user_input, functions.request_user_input_async, functions.exec, clock.sleep, collaboration.followup_task, collaboration.interrupt_agent, collaboration.list_agents, collaboration.send_message, collaboration.spawn_agent, collaboration.wait_agent\"}\nhook_rc=0\n"
rc=0
answer="functions.wait, functions.request_user_input, functions.request_user_input_async, functions.exec, clock.sleep, collaboration.followup_task, collaboration.interrupt_agent, collaboration.list_agents, collaboration.send_message, collaboration.spawn_agent, collaboration.wait_agent"
tool_items=[]
stderr_tail=""
```

#### b-codex-broker.log
```
argv=["codex", "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-s", "read-only", "-m", "gpt-6.1-sol", "-"]
rc=0
answer="functions.wait, functions.request_user_input, functions.request_user_input_async, functions.exec, clock.sleep, collaboration.followup_task, collaboration.interrupt_agent, collaboration.list_agents, collaboration.send_message, collaboration.spawn_agent, collaboration.wait_agent, apply_patch, clock__curr_time, create_goal, exec_command, get_goal, image_gen__imagegen, list_mcp_resource_templates, list_mcp_resources, mcp__codex_apps__chatgpt_space_attach_automation_to_page, mcp__codex_apps__chatgpt_space_create_canvas, mcp__codex_apps__chatgpt_space_create_controller_automation, mcp__codex_apps__chatgpt_space_create_page, mcp__codex_apps__chatgpt_space_create_page_visualization, mcp__codex_apps__chatgpt_space_create_presentation, mcp__codex_apps__chatgpt_space_create_space, mcp__codex_apps__chatgpt_space_create_spreadsheet, mcp__codex_apps__chatgpt_space_edit_page, mcp__codex_apps__chatgpt_space_execute_artifact_code, mcp__codex_apps__chatgpt_space_find_pages, mcp__codex_apps__chatgpt_space_get_artifact_execution_status, mcp__codex_apps__chatgpt_space_get_page_auto_update, mcp__codex_apps__chatgpt_space_get_page_sharing, mcp__codex_apps__chatgpt_space_get_sharing_availability, mcp__codex_apps__chatgpt_space_get_space, mcp__codex_apps__chatgpt_space_get_space_sharing, mcp__codex_apps__chatgpt_space_inspect_artifact, mcp__codex_apps__chatgpt_space_inspect_page_reference, mcp__codex_apps__chatgpt_space_list_page_automations, mcp__codex_apps__chatgpt_space_list_page_comments, mcp__codex_apps__chatgpt_space_list_pages, mcp__codex_apps__chatgpt_space_list_spaces, mcp__codex_apps__chatgpt_space_manage_page_comment, mcp__codex_apps__chatgpt_space_move_page, mcp__codex_apps__chatgpt_space_patch_page, mcp__codex_apps__chatgpt_space_progress_update, mcp__codex_apps__chatgpt_space_read_page, mcp__codex_apps__chatgpt_space_read_page_changes, mcp__codex_apps__chatgpt_space_read_page_reference, mcp__codex_apps__chatgpt_space_read_page_transcript, mcp__codex_apps__chatgpt_space_reply_page_comment, mcp__codex_apps__chatgpt_space_run_page_auto_update, mcp__codex_apps__chatgpt_space_search_sharing_recipients, mcp__codex_apps__chatgpt_space_set_page_auto_update, mcp__codex_apps__chatgpt_space_trash_page, mcp__codex_apps__chatgpt_space_update_page_sharing, mcp__codex_apps__chatgpt_space_update_space_sharing, mcp__codex_apps__chatgpt_space_upload_artifact_images, mcp__codex_apps__chatgpt_space_write_page_reference, mcp__codex_apps__codex_document_control_execute_document_command, mcp__codex_apps__codex_document_control_get_document_tool_schemas, mcp__codex_apps__codex_document_control_list_document_sessions, mcp__codex_apps__hotline_get_local_hotline, mcp__codex_apps__pets_adopt, mcp__codex_apps__pets_create_pet, mcp__codex_apps__pets_delete_pet, mcp__codex_apps__pets_get_pet_download_link, mcp__codex_apps__pets_list_pets, mcp__codex_apps__pets_prepare_pet_upload, mcp__codex_apps__pets_select_pet, mcp__codex_apps__pets_share_pet, mcp__codex_apps__pets_unshare_pet, mcp__codex_apps__pets_update_pet, mcp__codex_apps__pets_validate_pet_spritesheet, mcp__codex_apps__plugin_management_get_app_permissions, mcp__codex_apps__plugin_management_get_plugin_dependencies, mcp__codex_apps__plugin_management_search_plugins, mcp__codex_apps__plugin_management_suggest_plugins, mcp__codex_apps__plugin_management_uninstall_app, mcp__codex_apps__plugin_management_update_app_permissions, mcp__codex_apps__safety_settings_get_family_info, mcp__codex_apps__safety_settings_get_parental_controls, mcp__codex_apps__safety_settings_get_trusted_contact, mcp__codex_apps__safety_settings_prepare_parental_control_update, mcp__codex_apps__safety_settings_update_parental_control, mcp__codex_apps__search_service_web_run, mcp__codex_apps__sites_add_custom_domain, mcp__codex_apps__sites_change_site_slug, mcp__codex_apps__sites_create_schedule, mcp__codex_apps__sites_create_site, mcp__codex_apps__sites_create_source_repository_write_credential, mcp__codex_apps__sites_deploy_private_site_version, mcp__codex_apps__sites_deploy_site_version, mcp__codex_apps__sites_generate_siwc_bypass_token, mcp__codex_apps__sites_get_deployment_status, mcp__codex_apps__sites_get_environment_variables, mcp__codex_apps__sites_get_site, mcp__codex_apps__sites_get_site_version, mcp__codex_apps__sites_get_site_worker_logs, mcp__codex_apps__sites_list_custom_domains, mcp__codex_apps__sites_list_site_versions, mcp__codex_apps__sites_list_sites, mcp__codex_apps__sites_read_database_overview, mcp__codex_apps__sites_read_database_table_rows, mcp__codex_apps__sites_refresh_custom_domain_status, mcp__codex_apps__sites_remove_custom_domain, mcp__codex_apps__sites_save_site_version, mcp__codex_apps__sites_save_version_and_deploy_private, mcp__codex_apps__sites_update_environment_variables, mcp__codex_apps__sites_update_site_access, mcp__codex_apps__sites_update_site_metadata, read_mcp_resource, request_plugin_install, update_goal, view_image, web__run, write_stdin"
tool_items=[]
stderr_tail=""
```

### Step 3: Broker sentinel read (probe=0)

token_lines=1
```
argv=["codex", "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-s", "read-only", "-m", "gpt-6.1-sol", "-"]
rc=0
answer="TAF-SENTINEL-4b19e2"
tool_items=[{"type": "command_execution", "command": "/bin/zsh -lc 'cat /private/tmp/claude-502/taf/sentinel.txt'", "status": "completed"}]
stderr_tail=""
```

### Step 4: Claude self-reports (all probe=0)

advisor_lines: claude-grader=1, claude-fast=1, claude-hook=1

#### b-claude-grader.log
```
argv=["claude", "-p", "--system-prompt-file", "/private/tmp/claude-502/taf/grader-sys.txt", "--output-format", "json", "--json-schema", "{\"type\": \"object\", \"properties\": {\"answer\": {\"type\": \"string\"}}, \"required\": [\"answer\"], \"additionalProperties\": false}", "--model", "opus[1m]", "--dangerously-skip-permissions", "--strict-mcp-config", "--no-session-persistence", "--tools", "", "--settings", "{\"disableAllHooks\":true}"]
rc=0
answer="StructuredOutput, advisor"
tool_items=[]
stderr_tail=""
```

#### b-claude-fast.log
```
argv=["claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence", "--strict-mcp-config", "--settings", "{\"disableAllHooks\":true}", "--output-format", "json", "--json-schema", "{\"type\": \"object\", \"properties\": {\"answer\": {\"type\": \"string\"}}, \"required\": [\"answer\"], \"additionalProperties\": false}"]
rc=0
answer="StructuredOutput, advisor"
tool_items=[]
stderr_tail=""
```

#### b-claude-hook.log
```
argv=["bash", "-c", "<plan-validator.sh _call_fast_model>", "claude"]
hook_stdout="hook_model=haiku\n{\"answer\":\"StructuredOutput, advisor\"}\nhook_rc=0\n"
rc=0
answer="StructuredOutput, advisor"
tool_items=[]
stderr_tail=""
```

### Step 5: Hook env control (probe=0)

```
hook_rc=1
```

### Step 6: Grader rubric (all probe=0)

rubric_lines: claude single=1, claude batch=1, codex single=1, codex batch=1

#### b-rubric-claude-single.log
```
child=claude model=opus[1m] restricted=False
parsed={"grade": "D", "approved": false, "feedback": "Sections applied: \"6-Point Decision Checklist\", \"Robert's Three Laws of Testing\", \"Automatic F-Grade Triggers \u2014 Degradation Language\", \"Response Format\".\n\n\"6-Point Decision Checklist\": Q3 (trusting report vs verifying deliverable) fails. The report says 'both pass', but no test output is attached, so the claim is unverified. Q5 (checked before assuming) fails because nothing was checked. Q6 is unknown because the tests can't be judged without seeing them.\n\n\"Robert's Three Laws of Testing\": Can't confirm Law 1 (hard failures only: no skip/pending, no flaky assertions), Law 2 (unit tests before integration; claimed but unproven) or Law 3 (no mocks). The test code and run output are both missing.\n\n\"Automatic F-Grade Triggers \u2014 Degradation Language\": No banned terms found, so no auto-F.\n\nNeeded before approval:\n(1) Exact test command plus full raw output showing 2 tests ran and passed, with 0 skipped and 0 pending.\n(2) File path(s) and test function names.\n(3) Test source or diff, to confirm real parser behavior with no mocks and assertions that fail hard.\n(4) Proof the tests can fail: break the parser on purpose or show red-then-green output.\n\nStatus: blocked until the orchestrator checks the deliverable itself. Accepting this report as it stands would mean trusting a claim with no evidence behind it."}
```

#### b-rubric-claude-batch.log
```
child=claude model=opus[1m] restricted=False
parsed=[{"grade": "A", "approved": true, "feedback": "Applied sections: \"6-Point Decision Checklist\", \"Robert's Three Laws of Testing\", \"Automatic F-Grade Triggers \u2014 Degradation Language\", \"Response Format\". Checklist Q3 (trusting report vs verifying deliverable): the claim 'all 12 tests pass' comes with pytest output '12 passed', so the deliverable is backed by evidence. Laws of Testing: output shows hard passes and no skip/xfail. Mock usage and leaf-to-trunk order can't be checked from what was given, so check them if the test source is available. No degradation language found."}, {"grade": "F", "approved": false, "feedback": "Applied sections: \"6-Point Decision Checklist\", \"Robert's Three Laws of Testing\", \"Automatic F-Grade Triggers \u2014 Degradation Language\", \"Response Format\". Checklist Q3: the report says 'done' but gives no evidence. Accepting it means trusting a report, not verifying a deliverable. Checklist Q5: nothing was checked before assuming completion. Laws of Testing, 'Hard failures only': with no test run shown, pass/fail can't be confirmed, and an unverified pass counts as a silent pass. No degradation language found; the F is for a fundamentally wrong approach (no verification). Required fix: run the test suite, attach the full pytest output, and confirm hard passes with no skips."}]
```

#### b-rubric-codex-single.log
```
child=codex model=gpt-5.6-sol restricted=False
parsed={"grade": "C", "approved": false, "feedback": "Applied \"6-Point Decision Checklist\": report is unverified; no test output or deliverable evidence supports claimed pass. Applied \"Robert's Three Laws of Testing\": unit-test level fits leaf-to-trunk order, but attached evidence is required to verify hard failures and absence of mocks."}
```

#### b-rubric-codex-batch.log
```
child=codex model=gpt-5.6-sol restricted=False
parsed=[{"grade": "A", "approved": true, "feedback": "Applied \u201c6-Point Decision Checklist\u201d and \u201cRobert's Three Laws of Testing\u201d. Report includes direct pytest evidence: \u201c12 passed\u201d; verification supports completion, with no shown testing-law violation."}, {"grade": "D", "approved": false, "feedback": "Applied \u201c6-Point Decision Checklist\u201d, specifically whether deliverable was verified rather than report trusted. \u201cdone\u201d provides no test-run evidence, so completion is unverified."}]
```

### Step 7: Codex model rule (probe=0)

```
haiku configured=gpt-5.6-luna model_for=gpt-5.6-luna remote=n/a-no-host-parameter hook_newest=gpt-6-luna
sonnet configured=gpt-5.6-terra model_for=gpt-5.6-terra remote=n/a-no-host-parameter hook_newest=gpt-5.6-terra
opus configured=gpt-5.6-sol model_for=gpt-5.6-sol remote=n/a-no-host-parameter hook_newest=gpt-6.1-sol
fable configured=gpt-6-astra model_for=gpt-6-astra remote=n/a-no-host-parameter hook_newest=gpt-6-astra
brain brain_model=sonnet resolved=gpt-5.6-terra
```

### Step 8: State-manager suite

npm=0, tsc=0
```
189: Test Files  17 passed (17)
190:      Tests  272 passed (272)
```

Baseline failing-test set: empty.
Baseline tsc error set: empty (b-tsc.log is empty).

## After change

Measured 2026-10-17 by Task 8 after Tasks 2-7. Logs are verbatim from /private/tmp/claude-502/taf. All probes rc=0; no STOP condition hit.

### Counts: baseline vs after

- banned_lines: codex-grader 1 -> empty, codex-fast 1 -> empty, codex-hook 1 -> empty, codex-broker 1 -> empty
- token_lines (broker sentinel): 1 -> empty; tool_items baseline command_execution -> after only mcp_tool_call list_mcp_resources/list_mcp_resource_templates
- advisor_lines: claude-grader, claude-fast, claude-hook all empty after (baseline in Step 4 above)
- rubric_lines: claude single/batch 1 -> 1, codex single/batch 1 -> 1
- Claude grader child restricted=False -> restricted=True; Codex grader child model gpt-5.6-sol -> gpt-6.1-sol (equals opus hook_newest)
- Step 7 Commander suite: suite=0, 4034 passed in 651.67s, no FAILED/ERROR lines
- Step 8 hooks suite: 42 test files, all rc=0, no FAIL files (fail_files_rc=1)
- Step 9 state-manager: npm=0, Test Files 17 passed (17), Tests 273 passed (273) (baseline 272); no .loaded log needed (uptime at finish: load averages 12.48 16.09 17.01, test passed anyway)

#### a-codex-grader.log
```
argv=["codex", "exec", "-c", "model_reasoning_effort=\"low\"", "-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", "features.view_image=false", "-c", "features.apps=false", "-c", "features.browser_use=false", "-c", "features.computer_use=false", "-c", "features.multi_agent=false", "-c", "features.image_generation=false", "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false", "-c", "features.goals=false", "-c", "features.tool_suggest=false", "-c", "agents.enabled=false", "-c", "web_search=\"disabled\"", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--output-schema", "/private/tmp/claude-502/taf/tmppv6jdq1j.json", "-s", "read-only", "-m", "gpt-5.6-sol", "-"]
rc=0
answer="functions.wait, functions.request_user_input, functions.exec"
tool_items=[]
stderr_tail=""
```

#### a-codex-fast.log
```
argv=["codex", "exec", "-c", "model_reasoning_effort=\"low\"", "-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", "features.view_image=false", "-c", "features.apps=false", "-c", "features.browser_use=false", "-c", "features.computer_use=false", "-c", "features.multi_agent=false", "-c", "features.image_generation=false", "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false", "-c", "features.goals=false", "-c", "features.tool_suggest=false", "-c", "agents.enabled=false", "-c", "web_search=\"disabled\"", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--output-schema", "/private/tmp/claude-502/taf/tmpmqloxni6.json", "-s", "read-only", "-m", "gpt-5.6-luna", "-"]
rc=0
answer="functions.exec, functions.wait, functions.request_user_input, apply_patch, list_mcp_resource_templates, list_mcp_resources, read_mcp_resource"
tool_items=[]
stderr_tail=""
```

#### a-codex-hook.log
```
argv=["bash", "-c", "<plan-validator.sh _call_fast_model>", "codex"]
hook_stdout="hook_model=gpt-6-luna\n{\"answer\":\"functions.wait, functions.request_user_input, functions.request_user_input_async, functions.exec, clock.sleep\"}\nhook_rc=0\n"
rc=0
answer="functions.wait, functions.request_user_input, functions.request_user_input_async, functions.exec, clock.sleep"
tool_items=[]
stderr_tail=""
```

#### a-codex-broker.log
```
argv=["codex", "exec", "-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", "features.view_image=false", "-c", "features.apps=false", "-c", "features.browser_use=false", "-c", "features.computer_use=false", "-c", "features.multi_agent=false", "-c", "features.image_generation=false", "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false", "-c", "features.goals=false", "-c", "features.tool_suggest=false", "-c", "agents.enabled=false", "-c", "web_search=\"disabled\"", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-s", "read-only", "-m", "gpt-6.1-sol", "-"]
rc=0
answer="functions.wait, functions.request_user_input, functions.request_user_input_async, functions.exec, clock.sleep, apply_patch, clock__curr_time, list_mcp_resource_templates, list_mcp_resources, read_mcp_resource"
tool_items=[]
stderr_tail=""
```

#### a-broker-action.log
```
argv=["codex", "exec", "-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", "features.view_image=false", "-c", "features.apps=false", "-c", "features.browser_use=false", "-c", "features.computer_use=false", "-c", "features.multi_agent=false", "-c", "features.image_generation=false", "-c", "features.memories=false", "-c", "features.plugins=false", "-c", "features.hooks=false", "-c", "features.goals=false", "-c", "features.tool_suggest=false", "-c", "agents.enabled=false", "-c", "web_search=\"disabled\"", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-s", "read-only", "-m", "gpt-6.1-sol", "-"]
rc=0
answer="CANNOT"
tool_items=[{"type": "mcp_tool_call", "server": "codex", "tool": "list_mcp_resources", "status": "completed"}, {"type": "mcp_tool_call", "server": "codex", "tool": "list_mcp_resource_templates", "status": "completed"}]
stderr_tail=""
```

#### a-claude-grader.log
```
argv=["claude", "-p", "--system-prompt-file", "/private/tmp/claude-502/taf/grader-sys.txt", "--output-format", "json", "--json-schema", "{\"type\": \"object\", \"properties\": {\"answer\": {\"type\": \"string\"}}, \"required\": [\"answer\"], \"additionalProperties\": false}", "--model", "opus[1m]", "--restricted", "--strict-mcp-config", "--no-session-persistence", "--tools", "", "--settings", "{\"disableAllHooks\":true}"]
rc=0
answer="StructuredOutput"
tool_items=[]
stderr_tail=""
```

#### a-claude-fast.log
```
argv=["claude", "-p", "--model", "haiku", "--restricted", "--effort", "low", "--tools", "", "--no-session-persistence", "--strict-mcp-config", "--settings", "{\"disableAllHooks\":true}", "--output-format", "json", "--json-schema", "{\"type\": \"object\", \"properties\": {\"answer\": {\"type\": \"string\"}}, \"required\": [\"answer\"], \"additionalProperties\": false}"]
rc=0
answer="StructuredOutput"
tool_items=[]
stderr_tail=""
```

#### a-claude-hook.log
```
argv=["bash", "-c", "<plan-validator.sh _call_fast_model>", "claude"]
hook_stdout="hook_model=haiku\n{\"answer\":\"StructuredOutput\"}\nhook_rc=0\n"
rc=0
answer="StructuredOutput"
tool_items=[]
stderr_tail=""
```

#### a-hook-env.log
```
{"answer":"ok"}
hook_rc=0
```

#### a-rubric-claude-single.log
```
child=claude model=opus[1m] restricted=True
parsed={"grade": "C", "approved": false, "feedback": "Sections applied: \"6-Point Decision Checklist\", \"Robert's Three Laws of Testing\", \"Response Format\".\n\n\"6-Point Decision Checklist\": Q3 (trusting a report or verifying a deliverable?) fails. The worker says 'both pass' but attached no test output, so the claim is unverified. Q5 (checked before assuming?) also fails because nothing confirms the tests exist, run, or pass.\n\n\"Robert's Three Laws of Testing\": I can't check Law 1 (hard failures only, no skip/pending) or Law 3 (no mocks) without seeing the test code and output. Law 2 (leaf-to-trunk) is probably fine since these are unit tests, but that isn't confirmed.\n\nRequired before approval: attach the exact test command and its full output showing 2 passed, 0 skipped, 0 pending. Give the test file paths and names, and confirm the tests use the real parser with no mocks."}
```

#### a-rubric-claude-batch.log
```
child=claude model=opus[1m] restricted=True
parsed=[{"grade": "B", "approved": true, "feedback": "Applied '6-Point Decision Checklist' (Q3: trusting report vs verifying deliverable) and 'Robert's Three Laws of Testing'. Evidence given: pytest output '12 passed' matches the claim. Gaps: output doesn't show skips/xfail are absent (Law 1: hard failures only), whether mocks are used (Law 3), or unit-before-integration order (Law 2). Orchestrator should confirm full pytest summary line has no skipped/xfailed and that the tests cover the changed code. No degradation language ('Automatic F-Grade Triggers \u2014 Degradation Language' checked: none)."}, {"grade": "D", "approved": false, "feedback": "Applied '6-Point Decision Checklist' (Q3: trusting report vs verifying deliverable; Q5: checked before assuming) and 'Robert's Three Laws of Testing'. 'done' with no test run is an unverified report. Can't confirm Law 1 (hard failures only), Law 2 (leaf-to-trunk), or Law 3 (no mocks). Required: actual test command and full output proving pass with no skips. 'Automatic F-Grade Triggers \u2014 Degradation Language' checked: none present, so not F."}]
```

#### a-rubric-codex-single.log
```
child=codex model=gpt-6.1-sol restricted=False
parsed={"grade": "C", "approved": false, "feedback": "\"6-Point Decision Checklist\": Report claims two passing parser unit tests without attached output or verified deliverables. \"Robert's Three Laws of Testing\": Unit-test claim alone does not establish hard failures, leaf-to-trunk order, or absence of mocks."}
```

#### a-rubric-codex-batch.log
```
child=codex model=gpt-6.1-sol restricted=False
parsed=[{"grade": "A", "approved": true, "feedback": "\u201c6-Point Decision Checklist\u201d: pytest output \u201c12 passed\u201d substantiates worker\u2019s test-pass claim; does not establish broader correctness."}, {"grade": "D", "approved": false, "feedback": "\u201c6-Point Decision Checklist\u201d: \u201cdone\u201d supplies no test-run evidence; accepting completion would trust report without verifying deliverable."}]
```

#### a-models.log
```
haiku configured=gpt-5.6-luna model_for=gpt-6-luna remote=gpt-5.6-luna hook_newest=gpt-6-luna
sonnet configured=gpt-5.6-terra model_for=gpt-5.6-terra remote=gpt-5.6-terra hook_newest=gpt-5.6-terra
opus configured=gpt-5.6-sol model_for=gpt-6.1-sol remote=gpt-5.6-sol hook_newest=gpt-6.1-sol
fable configured=gpt-6-astra model_for=gpt-6-astra remote=gpt-6-astra hook_newest=gpt-6-astra
brain brain_model=sonnet resolved=gpt-5.6-terra
```

#### Step 4b: effort precedence
```
envhigh-flaglow rc=0
noenv-flaglow rc=0
probe=envhigh-flaglow efforts="effort":"high"
probe=noenv-flaglow efforts="effort":"low"
```
Matches expected: inherited CLAUDE_CODE_EFFORT_LEVEL beats --effort.

#### Steps 7-9 summaries
```
suite=0
4034 passed in 651.67s (0:10:51)
hooks: 42 files rc=0; fail_files_rc=1
npm=0
 Test Files  17 passed (17)
      Tests  273 passed (273)
```
