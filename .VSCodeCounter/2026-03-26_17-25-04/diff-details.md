# Diff Details

Date : 2026-03-26 17:25:04

Directory c:\\Users\\furkan.kiraz\\Desktop\\nl2sql\\app

Total : 75 files,  8253 codes, 844 comments, 1503 blanks, all 10600 lines

[Summary](results.md) / [Details](details.md) / [Diff Summary](diff.md) / Diff Details

## Files
| filename | language | code | comment | blank | total |
| :--- | :--- | ---: | ---: | ---: | ---: |
| [app/api/deps.py](/app/api/deps.py) | Python | 19 | -1 | 0 | 18 |
| [app/api/main.py](/app/api/main.py) | Python | 16 | 1 | 1 | 18 |
| [app/api/routes\_trace.py](/app/api/routes_trace.py) | Python | 99 | 51 | 38 | 188 |
| [app/core/config.py](/app/core/config.py) | Python | 9 | 0 | 1 | 10 |
| [app/core/data\_paths.py](/app/core/data_paths.py) | Python | 63 | 14 | 27 | 104 |
| [app/domain/execution\_models.py](/app/domain/execution_models.py) | Python | 3 | 0 | 0 | 3 |
| [app/domain/semantic\_models.py](/app/domain/semantic_models.py) | Python | 31 | 4 | 5 | 40 |
| [app/domain/trace\_models.py](/app/domain/trace_models.py) | Python | 128 | 40 | 22 | 190 |
| [app/providers/catalog/in\_memory.py](/app/providers/catalog/in_memory.py) | Python | -172 | -7 | -6 | -185 |
| [app/providers/executor/oracle\_executor.py](/app/providers/executor/oracle_executor.py) | Python | 22 | 0 | 0 | 22 |
| [app/providers/llm/mock\_llm.py](/app/providers/llm/mock_llm.py) | Python | 5 | 0 | 0 | 5 |
| [app/providers/llm/openai\_compatible.py](/app/providers/llm/openai_compatible.py) | Python | 321 | 0 | 40 | 361 |
| [app/providers/llm/prompts.py](/app/providers/llm/prompts.py) | Python | 73 | 3 | 7 | 83 |
| [app/providers/retrieval/embedding\_retriever.py](/app/providers/retrieval/embedding_retriever.py) | Python | 18 | 7 | 1 | 26 |
| [app/providers/retrieval/gating\_policy.py](/app/providers/retrieval/gating_policy.py) | Python | 45 | 72 | 15 | 132 |
| [app/providers/retrieval/hybrid\_retriever.py](/app/providers/retrieval/hybrid_retriever.py) | Python | 15 | 10 | 3 | 28 |
| [app/providers/retrieval/in\_memory\_doc\_retriever.py](/app/providers/retrieval/in_memory_doc_retriever.py) | Python | 20 | 0 | 3 | 23 |
| [app/providers/retrieval/in\_memory\_retriever.py](/app/providers/retrieval/in_memory_retriever.py) | Python | 102 | 0 | 10 | 112 |
| [app/semantic/models.py](/app/semantic/models.py) | Python | 6 | 8 | 4 | 18 |
| [app/semantic/registry.py](/app/semantic/registry.py) | Python | 22 | 2 | 2 | 26 |
| [app/semantic/repository.py](/app/semantic/repository.py) | Python | 30 | 0 | 0 | 30 |
| [app/services/catalog\_embedding\_indexer.py](/app/services/catalog_embedding_indexer.py) | Python | 0 | 7 | 0 | 7 |
| [app/services/catalog\_service.py](/app/services/catalog_service.py) | Python | 5 | 0 | 1 | 6 |
| [app/services/clarification\_decision\_service.py](/app/services/clarification_decision_service.py) | Python | 160 | 0 | 12 | 172 |
| [app/services/document\_retrieval\_service.py](/app/services/document_retrieval_service.py) | Python | 134 | 0 | 13 | 147 |
| [app/services/example\_embedding\_indexer.py](/app/services/example_embedding_indexer.py) | Python | 164 | 6 | 31 | 201 |
| [app/services/execution\_risk.py](/app/services/execution_risk.py) | Python | 70 | 0 | 10 | 80 |
| [app/services/filter\_column\_resolution\_service.py](/app/services/filter_column_resolution_service.py) | Python | 205 | 131 | 51 | 387 |
| [app/services/grounding\_config\_provider.py](/app/services/grounding_config_provider.py) | Python | 120 | 87 | 47 | 254 |
| [app/services/intent\_guard.py](/app/services/intent_guard.py) | Python | 120 | 0 | 17 | 137 |
| [app/services/narrator\_service.py](/app/services/narrator_service.py) | Python | 88 | 1 | 6 | 95 |
| [app/services/orchestrator.py](/app/services/orchestrator.py) | Python | 526 | 32 | 31 | 589 |
| [app/services/plan\_normalizer.py](/app/services/plan_normalizer.py) | Python | 21 | 1 | 2 | 24 |
| [app/services/planner\_service.py](/app/services/planner_service.py) | Python | 153 | 3 | 13 | 169 |
| [app/services/planning\_context\_service.py](/app/services/planning_context_service.py) | Python | 69 | 5 | 9 | 83 |
| [app/services/planning\_models.py](/app/services/planning_models.py) | Python | 13 | 0 | 0 | 13 |
| [app/services/prompt\_assembly\_service.py](/app/services/prompt_assembly_service.py) | Python | 39 | 6 | 3 | 48 |
| [app/services/query\_understanding.py](/app/services/query_understanding.py) | Python | 142 | 28 | 20 | 190 |
| [app/services/schema\_retrieval\_service.py](/app/services/schema_retrieval_service.py) | Python | 9 | 0 | 1 | 10 |
| [app/services/semantic\_embedding\_indexer.py](/app/services/semantic_embedding_indexer.py) | Python | 253 | 6 | 37 | 296 |
| [app/services/semantic\_planning.py](/app/services/semantic_planning.py) | Python | 598 | 0 | 97 | 695 |
| [app/services/semantic\_resolution\_service.py](/app/services/semantic_resolution_service.py) | Python | 29 | 0 | 0 | 29 |
| [app/services/sql\_compiler.py](/app/services/sql_compiler.py) | Python | -5 | 0 | -2 | -7 |
| [app/services/trace\_serializer.py](/app/services/trace_serializer.py) | Python | 319 | 51 | 75 | 445 |
| [app/services/validation\_repair\_service.py](/app/services/validation_repair_service.py) | Python | 298 | 22 | 45 | 365 |
| [app/static/pipeline\_view.html](/app/static/pipeline_view.html) | HTML | 775 | 2 | 96 | 873 |
| [app/tests/test\_config\_sprint3.py](/app/tests/test_config_sprint3.py) | Python | 9 | 0 | 0 | 9 |
| [app/tests/test\_document\_retrieval\_service.py](/app/tests/test_document_retrieval_service.py) | Python | 67 | 0 | 6 | 73 |
| [app/tests/test\_execution\_risk.py](/app/tests/test_execution_risk.py) | Python | 144 | 0 | 32 | 176 |
| [app/tests/test\_filter\_column\_resolution.py](/app/tests/test_filter_column_resolution.py) | Python | 282 | 71 | 81 | 434 |
| [app/tests/test\_index\_build\_scripts.py](/app/tests/test_index_build_scripts.py) | Python | 15 | 0 | 9 | 24 |
| [app/tests/test\_narrator\_leakage.py](/app/tests/test_narrator_leakage.py) | Python | 20 | 0 | 8 | 28 |
| [app/tests/test\_narrator\_service.py](/app/tests/test_narrator_service.py) | Python | 61 | 0 | 17 | 78 |
| [app/tests/test\_oracle\_executor.py](/app/tests/test_oracle_executor.py) | Python | 88 | 0 | 27 | 115 |
| [app/tests/test\_orchestrator\_execution\_guard.py](/app/tests/test_orchestrator_execution_guard.py) | Python | 115 | 0 | 20 | 135 |
| [app/tests/test\_orchestrator\_smoke.py](/app/tests/test_orchestrator_smoke.py) | Python | 68 | 0 | 16 | 84 |
| [app/tests/test\_pipeline\_live\_view.py](/app/tests/test_pipeline_live_view.py) | Python | 479 | 77 | 138 | 694 |
| [app/tests/test\_plan\_normalizer.py](/app/tests/test_plan_normalizer.py) | Python | 28 | 0 | 3 | 31 |
| [app/tests/test\_planner\_service.py](/app/tests/test_planner_service.py) | Python | 106 | -4 | 24 | 126 |
| [app/tests/test\_planning\_stages.py](/app/tests/test_planning_stages.py) | Python | 112 | 0 | 10 | 122 |
| [app/tests/test\_prompt\_budget.py](/app/tests/test_prompt_budget.py) | Python | 44 | 0 | 4 | 48 |
| [app/tests/test\_query\_understanding.py](/app/tests/test_query_understanding.py) | Python | 127 | 31 | 27 | 185 |
| [app/tests/test\_retrieval\_artifact\_separation.py](/app/tests/test_retrieval_artifact_separation.py) | Python | 112 | 0 | 34 | 146 |
| [app/tests/test\_retrieval\_upgrade.py](/app/tests/test_retrieval_upgrade.py) | Python | 289 | 64 | 84 | 437 |
| [app/tests/test\_schema\_retrieval.py](/app/tests/test_schema_retrieval.py) | Python | 97 | 0 | 11 | 108 |
| [app/tests/test\_select\_columns\_defaults.py](/app/tests/test_select_columns_defaults.py) | Python | 31 | 0 | 3 | 34 |
| [app/tests/test\_semantic\_planning.py](/app/tests/test_semantic_planning.py) | Python | 260 | 0 | 39 | 299 |
| [app/tests/test\_semantic\_registry.py](/app/tests/test_semantic_registry.py) | Python | 5 | 0 | 2 | 7 |
| [app/tests/test\_sprint\_c.py](/app/tests/test_sprint_c.py) | Python | 40 | 0 | 7 | 47 |
| [app/tests/test\_sql\_compiler.py](/app/tests/test_sql_compiler.py) | Python | 69 | 0 | 18 | 87 |
| [app/tests/test\_structured\_output\_firewall.py](/app/tests/test_structured_output_firewall.py) | Python | 107 | 0 | 35 | 142 |
| [app/tests/test\_validation\_repair\_service.py](/app/tests/test_validation_repair_service.py) | Python | 156 | 0 | 28 | 184 |
| [app/tests/test\_validation\_service.py](/app/tests/test_validation_service.py) | Python | -1 | 1 | 0 | 0 |
| [app/utils/date\_literals.py](/app/utils/date_literals.py) | Python | 132 | 1 | 30 | 163 |
| [app/utils/turkish.py](/app/utils/turkish.py) | Python | 11 | 11 | 2 | 24 |

[Summary](results.md) / [Details](details.md) / [Diff Summary](diff.md) / Diff Details