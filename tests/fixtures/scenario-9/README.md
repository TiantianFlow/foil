# Scenario 9

A launched lead asks the human a question: it writes the question in `board/status.md` and sends `user` a pointer to it. The human's agent is its own process outside the fleet. It waits in the background for the lead's mail, reads the question, answers with `foil send lead`, and waits again for the result.
