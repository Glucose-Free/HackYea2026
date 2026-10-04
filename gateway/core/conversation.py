from dataclasses import dataclass

USER_ROLE = "user"


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

    def _get_latest_user_message_index(self) -> int | None:
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].role == USER_ROLE:
                return index
        return None
