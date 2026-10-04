from dataclasses import dataclass

USER_ROLE = "user"
ASSISTANT_ROLE = "assistant"


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class Conversation:
    messages: tuple[Message, ...]

    def get_latest_user_message(self) -> Message | None:
        index = self._get_latest_user_message_index()
        return None if index is None else self.messages[index]

    def get_context_before_latest_user_message(self, window: int) -> tuple[Message, ...]:
        index = self._get_latest_user_message_index()
        if index is None or window <= 0:
            return ()
        return self.messages[max(0, index - window):index]

    def get_model_turns(self, gateway_non_answers: frozenset[str]) -> "Conversation":
        """The history the model may see: up to the latest user message, minus turns the gateway did not answer.

        A refused prompt stays in the client's history; replaying it would let "do what I asked above" carry it
        past checkpoint 1, which judges only the latest message. Anything after the latest user message was
        never judged at all, so it is dropped too.
        """
        index = self._get_latest_user_message_index()
        if index is None:
            return Conversation(())
        judged = self.messages[:index + 1]
        kept: list[Message] = []
        position = 0
        while position < len(judged):
            message = judged[position]
            following = judged[position + 1] if position + 1 < len(judged) else None
            if message.role == USER_ROLE and following is not None and is_gateway_non_answer(following, gateway_non_answers):
                position += 2
                continue
            kept.append(message)
            position += 1
        return Conversation(tuple(kept))

    def _get_latest_user_message_index(self) -> int | None:
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].role == USER_ROLE:
                return index
        return None


def is_gateway_non_answer(message: Message, gateway_non_answers: frozenset[str]) -> bool:
    # Clients may trim or re-wrap the reply they stored, so compare stripped text.
    return message.role == ASSISTANT_ROLE and message.content.strip() in gateway_non_answers
