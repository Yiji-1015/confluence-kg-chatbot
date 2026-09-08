package com.yiji.Chatbot.exception;

/**
 * Python AI Engine 호출이 실패했을 때 던진다.
 *
 * 원인 예외는 cause로만 들고 다닌다. 메시지에 담아 사용자 응답으로 흘리면
 * 내부 URL과 예외 클래스명이 그대로 노출된다.
 */
public class AiEngineException extends RuntimeException {

    public AiEngineException(String message, Throwable cause) {
        super(message, cause);
    }
}
