package com.yiji.Chatbot.exception;

/**
 * 요청한 대화방이 없거나, 요청자의 것이 아닐 때 던진다.
 *
 * 소유자가 다른 경우에도 "없음"으로 취급한다. 403으로 답하면 "그 대화방은 존재한다"는
 * 사실이 새어나가기 때문이다. (근거는 CHANGELOG 2026-09-01)
 *
 * 웹 계층 타입(HttpStatus 등)을 의도적으로 참조하지 않는다. 상태 코드로의 번역은
 * GlobalExceptionHandler 한 곳에서만 한다.
 */
public class SessionNotFoundException extends RuntimeException {

    public SessionNotFoundException(String sessionId) {
        super("대화방을 찾을 수 없습니다. (sessionId: " + sessionId + ")");
    }
}
