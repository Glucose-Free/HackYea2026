from gateway.core.conversation import Conversation, Message


def build_conversation(*role_content_pairs: tuple[str, str]) -> Conversation:
    return Conversation(tuple(Message(role, content) for role, content in role_content_pairs))


def test_latest_user_message_skips_trailing_assistant_messages():
    conversation = build_conversation(("user", "first"), ("assistant", "reply"), ("user", "second"), ("assistant", "x"))
    assert conversation.get_latest_user_message() == Message("user", "second")


def test_latest_user_message_is_none_without_user_messages():
    assert build_conversation(("system", "s"), ("assistant", "a")).get_latest_user_message() is None


def test_context_before_latest_user_message_respects_window():
    conversation = build_conversation(("user", "1"), ("assistant", "2"), ("user", "3"), ("assistant", "4"), ("user", "5"))
    assert conversation.get_context_before_latest_user_message(2) == (Message("user", "3"), Message("assistant", "4"))


def test_context_is_empty_for_zero_window_or_first_message():
    conversation = build_conversation(("user", "only"))
    assert conversation.get_context_before_latest_user_message(10) == ()
    assert build_conversation(("user", "a"), ("user", "b")).get_context_before_latest_user_message(0) == ()


GATEWAY_NON_ANSWERS = frozenset({"refused.", "failed."})


def test_model_turns_drop_each_user_turn_the_gateway_did_not_answer_with_its_non_answer():
    conversation = build_conversation(
        ("user", "bad"), ("assistant", "refused."), ("user", "ok"), ("assistant", "answer"),
        ("user", "outage"), ("assistant", " failed.\n"), ("user", "latest"),
    )
    assert conversation.get_model_turns(GATEWAY_NON_ANSWERS).messages == (
        Message("user", "ok"), Message("assistant", "answer"), Message("user", "latest"),
    )


def test_model_turns_end_at_the_latest_user_message():
    conversation = build_conversation(("user", "hi"), ("assistant", "prefill"))
    assert conversation.get_model_turns(GATEWAY_NON_ANSWERS).messages == (Message("user", "hi"),)


def test_model_turns_keep_an_assistant_message_that_only_resembles_a_non_answer():
    conversation = build_conversation(("user", "a"), ("assistant", "refused. but here is more"), ("user", "b"))
    assert len(conversation.get_model_turns(GATEWAY_NON_ANSWERS).messages) == 3
