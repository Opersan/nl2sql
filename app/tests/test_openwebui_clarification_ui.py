from app.openwebui_clarification_ui import (
    apply_embed_to_body,
    build_clarification_embed_html,
    extract_clarification_view,
)


def test_extract_clarification_view_from_structured_payload() -> None:
    body = {
        "session_id": "owui-conv-123",
        "status": "clarification",
        "clarification_payload": {
            "clarification_id": "clar-123",
            "message": "Hangi unvani kastediyorsunuz?",
            "options": [
                {"index": 1, "label": "Proje Yoneticisi", "value": "Proje Yoneticisi"},
                {"index": 2, "label": "Sistem Yoneticisi", "value": "Sistem Yoneticisi"},
            ],
        },
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "fallback",
                    "metadata": {
                        "status": "clarification",
                        "session_id": "owui-conv-123",
                        "clarification_id": "clar-123",
                    },
                }
            }
        ],
    }

    clarification = extract_clarification_view(body)

    assert clarification is not None
    assert clarification.question == "Hangi unvani kastediyorsunuz?"
    assert clarification.clarification_id == "clar-123"
    assert clarification.session_id == "owui-conv-123"
    assert [choice.label for choice in clarification.choices] == [
        "Proje Yoneticisi",
        "Sistem Yoneticisi",
        "Sen karar ver",
    ]


def test_extract_clarification_view_from_rendered_content_fallback() -> None:
    body = {
        "status": "clarification",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": (
                        "Hangi alanlari gormek istediginizi belirtir misiniz?\n\n"
                        "1. Proje Yoneticisi\n"
                        "2. Sistem Yoneticisi\n"
                        "3. Sen karar ver\n\n"
                        'Yanit olarak "1", secenek adi veya "sen karar ver" yazabilirsiniz.'
                    ),
                    "metadata": {
                        "status": "clarification",
                        "clarification_id": "clar-abc",
                    },
                }
            }
        ],
    }

    clarification = extract_clarification_view(body)

    assert clarification is not None
    assert clarification.question == "Hangi alanlari gormek istediginizi belirtir misiniz?"
    assert [choice.value for choice in clarification.choices] == [
        "1",
        "2",
        "sen karar ver",
    ]


def test_extract_clarification_view_from_top_level_metadata_and_output() -> None:
    body = {
        "session_id": "owui-conv-top",
        "metadata": {
            "status": "clarification",
            "clarification_id": "clar-top",
            "session_id": "owui-conv-top",
        },
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            "Hangi alanlari gormek istediginizi belirtir misiniz?\n\n"
                            "1. Proje Yoneticisi\n"
                            "2. Sistem Yoneticisi\n"
                            "3. Sen karar ver\n\n"
                            'Yanit olarak "1", secenek adi veya "sen karar ver" yazabilirsiniz.'
                        ),
                    }
                ],
            }
        ],
    }

    clarification = extract_clarification_view(body)

    assert clarification is not None
    assert clarification.session_id == "owui-conv-top"
    assert clarification.clarification_id == "clar-top"
    assert [choice.value for choice in clarification.choices] == [
        "1",
        "2",
        "sen karar ver",
    ]


def test_extract_clarification_view_from_plain_text_heuristic_without_metadata() -> None:
    body = {
        "content": (
            "Hangi alanlari gormek istediginizi belirtir misiniz?\n\n"
            "1. Proje Yoneticisi\n"
            "2. Sistem Yoneticisi\n"
            "3. Sen karar ver\n\n"
            'Yanit olarak "1", secenek adi veya "sen karar ver" yazabilirsiniz.'
        )
    }

    clarification = extract_clarification_view(body)

    assert clarification is not None
    assert clarification.question == "Hangi alanlari gormek istediginizi belirtir misiniz?"


def test_build_clarification_embed_html_uses_openwebui_prompt_fill_and_submit() -> None:
    body = {
        "status": "clarification",
        "clarification_payload": {
            "clarification_id": "clar-1",
            "message": "Dizayn icin hangi birimi kastediyorsunuz?",
            "options": [
                {"index": 1, "label": "ELEKTRIK DIZAYN", "value": "ELEKTRIK DIZAYN"},
                {"index": 2, "label": "MEKANIK DIZAYN", "value": "MEKANIK DIZAYN"},
            ],
        },
    }

    clarification = extract_clarification_view(body)
    assert clarification is not None

    html = build_clarification_embed_html(clarification)

    assert "input:prompt" in html
    assert "action:submit" in html
    assert "ELEKTRIK DIZAYN" in html
    assert "MEKANIK DIZAYN" in html
    assert "sen karar ver" in html.lower()
    assert "iframe:height" in html


def test_apply_embed_to_body_clears_assistant_text_and_attaches_embed() -> None:
    original = {
        "content": "Hangi alanlari gormek istediginizi belirtir misiniz?",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Hangi alanlari gormek istediginizi belirtir misiniz?"}],
            }
        ],
        "messages": [
            {"role": "user", "content": "yonetici unvanli calisanlari goster"},
            {
                "role": "assistant",
                "content": "Hangi alanlari gormek istediginizi belirtir misiniz?",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hangi alanlari gormek istediginizi belirtir misiniz?"}]}],
            },
        ],
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Hangi alanlari gormek istediginizi belirtir misiniz?",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hangi alanlari gormek istediginizi belirtir misiniz?"}]}],
                }
            }
        ],
    }

    updated = apply_embed_to_body(original, "<html>embed</html>")

    assert updated["content"] == ""
    assert updated["messages"][-1]["content"] == ""
    assert updated["messages"][-1]["embeds"] == ["<html>embed</html>"]
    assert updated["messages"][-1]["output"][0]["content"][0]["text"] == ""
    assert updated["choices"][0]["message"]["content"] == ""
    assert updated["choices"][0]["message"]["output"][0]["content"][0]["text"] == ""
    assert updated["output"][0]["content"][0]["text"] == ""
    assert updated["embeds"] == ["<html>embed</html>"]
