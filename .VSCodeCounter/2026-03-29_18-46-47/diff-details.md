# Diff Details

Date : 2026-03-29 18:46:47

Directory c:\\Users\\furkan.kiraz\\Desktop\\nl2sql\\app

Total : 50 files,  7709 codes, 311 comments, 1085 blanks, all 9105 lines

[Summary](results.md) / [Details](details.md) / [Diff Summary](diff.md) / Diff Details

## Files
| filename | language | code | comment | blank | total |
| :--- | :--- | ---: | ---: | ---: | ---: |
| [app/api/deps.py](/app/api/deps.py) | Python | 11 | 4 | -1 | 14 |
| [app/api/main.py](/app/api/main.py) | Python | 26 | 8 | 10 | 44 |
| [app/api/routes\_chat.py](/app/api/routes_chat.py) | Python | 384 | -12 | 66 | 438 |
| [app/api/routes\_trace.py](/app/api/routes_trace.py) | Python | 14 | -49 | -8 | -43 |
| [app/api/routes\_viewer.py](/app/api/routes_viewer.py) | Python | 123 | 28 | 40 | 191 |
| [app/api/schemas.py](/app/api/schemas.py) | Python | 32 | 12 | 10 | 54 |
| [app/domain/models.py](/app/domain/models.py) | Python | 17 | 18 | 12 | 47 |
| [app/domain/trace\_models.py](/app/domain/trace_models.py) | Python | 39 | 1 | 3 | 43 |
| [app/openwebui\_clarification\_ui.py](/app/openwebui_clarification_ui.py) | Python | 439 | 12 | 62 | 513 |
| [app/providers/catalog/in\_memory.py](/app/providers/catalog/in_memory.py) | Python | 172 | 7 | 6 | 185 |
| [app/providers/llm/base.py](/app/providers/llm/base.py) | Python | 0 | 3 | 0 | 3 |
| [app/providers/llm/mock\_llm.py](/app/providers/llm/mock_llm.py) | Python | 8 | 2 | 2 | 12 |
| [app/providers/llm/openai\_compatible.py](/app/providers/llm/openai_compatible.py) | Python | -9 | 0 | 0 | -9 |
| [app/providers/llm/prompts.py](/app/providers/llm/prompts.py) | Python | 9 | 0 | 0 | 9 |
| [app/providers/run\_store.py](/app/providers/run_store.py) | Python | 558 | 79 | 63 | 700 |
| [app/services/clarification\_decision\_service.py](/app/services/clarification_decision_service.py) | Python | 9 | 5 | 2 | 16 |
| [app/services/clarification\_state\_manager.py](/app/services/clarification_state_manager.py) | Python | 262 | 47 | 37 | 346 |
| [app/services/document\_retrieval\_service.py](/app/services/document_retrieval_service.py) | Python | -1 | 0 | 0 | -1 |
| [app/services/filter\_column\_resolution\_service.py](/app/services/filter_column_resolution_service.py) | Python | 120 | -2 | 3 | 121 |
| [app/services/filter\_value\_profile\_provider.py](/app/services/filter_value_profile_provider.py) | Python | 147 | 0 | 22 | 169 |
| [app/services/filter\_value\_resolution\_service.py](/app/services/filter_value_resolution_service.py) | Python | 631 | 32 | 53 | 716 |
| [app/services/grounding\_config\_provider.py](/app/services/grounding_config_provider.py) | Python | 19 | 15 | 3 | 37 |
| [app/services/narrator\_service.py](/app/services/narrator_service.py) | Python | 29 | 12 | 11 | 52 |
| [app/services/orchestrator.py](/app/services/orchestrator.py) | Python | 436 | 19 | 43 | 498 |
| [app/services/planner\_service.py](/app/services/planner_service.py) | Python | 29 | -1 | 2 | 30 |
| [app/services/prompt\_assembly\_service.py](/app/services/prompt_assembly_service.py) | Python | -1 | 0 | 0 | -1 |
| [app/services/query\_plan\_repair.py](/app/services/query_plan_repair.py) | Python | -1 | 0 | 0 | -1 |
| [app/services/session\_service.py](/app/services/session_service.py) | Python | -4 | -15 | -2 | -21 |
| [app/services/sql\_compiler.py](/app/services/sql_compiler.py) | Python | -3 | 0 | 0 | -3 |
| [app/services/trace\_serializer.py](/app/services/trace_serializer.py) | Python | 84 | 7 | 3 | 94 |
| [app/services/validation\_repair\_service.py](/app/services/validation_repair_service.py) | Python | -59 | -7 | -3 | -69 |
| [app/static/pipeline\_live\_view.html](/app/static/pipeline_live_view.html) | HTML | 1,029 | 3 | 51 | 1,083 |
| [app/static/pipeline\_view.html](/app/static/pipeline_view.html) | HTML | -698 | -2 | -88 | -788 |
| [app/static/pipeline\_view\_legacy.html](/app/static/pipeline_view_legacy.html) | HTML | 1,335 | 0 | 175 | 1,510 |
| [app/tests/test\_chat\_clarification\_contract.py](/app/tests/test_chat_clarification_contract.py) | Python | 84 | 1 | 18 | 103 |
| [app/tests/test\_filter\_column\_resolution.py](/app/tests/test_filter_column_resolution.py) | Python | 283 | -45 | 15 | 253 |
| [app/tests/test\_filter\_value\_resolution.py](/app/tests/test_filter_value_resolution.py) | Python | 244 | 7 | 58 | 309 |
| [app/tests/test\_grounding\_workflow.py](/app/tests/test_grounding_workflow.py) | Python | 896 | 88 | 206 | 1,190 |
| [app/tests/test\_mock\_llm\_provider.py](/app/tests/test_mock_llm_provider.py) | Python | 8 | 0 | 1 | 9 |
| [app/tests/test\_narrator\_service.py](/app/tests/test_narrator_service.py) | Python | 21 | 10 | -2 | 29 |
| [app/tests/test\_openai\_provider\_connectivity.py](/app/tests/test_openai_provider_connectivity.py) | Python | 44 | 0 | 16 | 60 |
| [app/tests/test\_openwebui\_chat\_integration.py](/app/tests/test_openwebui_chat_integration.py) | Python | 301 | 6 | 44 | 351 |
| [app/tests/test\_openwebui\_clarification\_ui.py](/app/tests/test_openwebui_clarification_ui.py) | Python | 176 | 0 | 26 | 202 |
| [app/tests/test\_pipeline\_live\_view.py](/app/tests/test_pipeline_live_view.py) | Python | 119 | 0 | 23 | 142 |
| [app/tests/test\_planner\_service.py](/app/tests/test_planner_service.py) | Python | 33 | 0 | 7 | 40 |
| [app/tests/test\_run\_store.py](/app/tests/test_run_store.py) | Python | 188 | 16 | 53 | 257 |
| [app/tests/test\_startup\_wiring.py](/app/tests/test_startup_wiring.py) | Python | 47 | 2 | 17 | 66 |
| [app/tests/test\_validation\_service.py](/app/tests/test_validation_service.py) | Python | 1 | -1 | 0 | 0 |
| [app/tests/test\_viewer\_api.py](/app/tests/test_viewer_api.py) | Python | 82 | 11 | 25 | 118 |
| [app/utils/turkish.py](/app/utils/turkish.py) | Python | -4 | -10 | 1 | -13 |

[Summary](results.md) / [Details](details.md) / [Diff Summary](diff.md) / Diff Details