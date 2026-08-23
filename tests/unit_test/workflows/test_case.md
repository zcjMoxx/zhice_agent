# Workflow adapter tests

- `test_compile_and_roundtrip_restricted_flow`: verifies the portable Node-RED representation preserves the reviewed ZhiCe node types, positions, configuration, and wires.
- `test_rejects_arbitrary_node_red_nodes`: verifies Function/exec/other unreviewed Node-RED nodes cannot enter the runtime.
- `test_tool_catalog_only_returns_live_allowlisted_tools`: verifies the editor receives only tools that are both registered for the current actor and present in the workflow allowlist, and that the read-only test path executes the selected live provider while denying tools outside the allowlist.
- `test_missing_workflow_api_operations_return_stable_404`: verifies run, pause, resume, and delete convert missing owner-scoped workflows into the stable `WORKFLOW_NOT_FOUND` 404 response boundary instead of leaking a server exception.
- `test_scheduler_recovers_stale_windows_process_lock`: verifies a Windows scheduler start replaces a lock owned by a nonexistent PID without using `os.kill(pid, 0)` or failing with `WinError 87`.
- `test_scheduler_recovers_reused_windows_process_id_lock`: verifies a legacy Windows lock is replaced when its live PID belongs to a process created after the lock file, covering OS PID reuse without terminating the unrelated process.
- `test_tool_inputs.py`: verifies user-facing place names are resolved through the allowlisted geocoder before weather calls, and Xiaohongshu note links are safely converted to detail arguments while rejecting non-HTTPS, foreign-domain, or incomplete links.
- `test_store_publish_is_idempotent_and_owner_scoped`: verifies publishing the same unchanged version is safe to repeat while owner isolation remains enforced.
- `test_editing_published_workflow_creates_next_draft_version`: verifies editing active v1 automatically creates draft v2, repeated saves stay on v2, and publishing v2 clears the pending-publish state.
- `test_state_detects_legacy_same_version_draft_content_change`: verifies historical rows whose draft and active version numbers match but whose actual canvas content differs are still marked ready to publish.
- `test_executor_uses_direct_graph_input_instead_of_stale_delivery_reference`: verifies intelligent processing and delivery consume their unique direct predecessor output, so an old saved source reference cannot send raw provider JSON after a node is inserted.
- `test_all_user_facing_processing_handlers_are_reachable`: verifies official email, personal email, and owner QQ delivery convert Markdown emphasis and lists into readable plain text before invoking providers.
- `test_publish_rechecks_current_qq_binding_and_consent`: verifies QQ delivery cannot publish without explicit consent and revalidates the current owner's live QQ notification capability.
- `test_qq_timeout_is_outcome_unknown_and_is_not_retried`: verifies an unconfirmed QQ send is recorded as an unknown external outcome and is attempted only once.
- Node-RED round-trip coverage includes the fixed `qq_notification` node and still rejects arbitrary external nodes.
