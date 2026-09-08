package com.yiji.Chatbot.exception;

import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.context.request.WebRequest;
import org.springframework.web.servlet.mvc.method.annotation.ResponseEntityExceptionHandler;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 전역 예외 처리기.
 *
 * 응답 형식은 RFC 9457 ProblemDetail(application/problem+json)로 통일한다.
 * 컨트롤러가 예외를 잡아 응답을 조립하지 않게 하는 것이 목적이다.
 *
 * ResponseEntityExceptionHandler를 상속하면 Spring MVC 표준 예외
 * (본문 파싱 실패, 필수 파라미터 누락, 허용되지 않은 메서드 등)의 처리가 이미 들어 있다.
 * 우리가 손볼 것은 사용자에게 보일 문장이 비어 있는 두 곳뿐이다.
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler extends ResponseEntityExceptionHandler {

    /**
     * @Valid 실패(400).
     *
     * 기본 detail은 "Invalid request content."라서 어느 필드가 왜 틀렸는지 알려주지 않는다.
     * DTO에 적어둔 메시지("질문은 최대 1000자까지 입력 가능합니다.")를 detail로 올려
     * 프론트가 그대로 보여줄 수 있게 한다. 필드가 여럿이면 errors에 전부 담는다.
     */
    @Override
    protected ResponseEntity<Object> handleMethodArgumentNotValid(
            MethodArgumentNotValidException ex,
            HttpHeaders headers,
            HttpStatusCode status,
            WebRequest request) {

        Map<String, String> fieldErrors = new LinkedHashMap<>();
        for (FieldError fieldError : ex.getBindingResult().getFieldErrors()) {
            // 한 필드에 제약이 여러 개 걸려 있으면 먼저 걸린 메시지를 남긴다.
            fieldErrors.putIfAbsent(fieldError.getField(), fieldError.getDefaultMessage());
        }

        String detail = fieldErrors.values().stream()
                .findFirst()
                .orElse("요청 값이 올바르지 않습니다.");

        ProblemDetail body = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, detail);
        body.setTitle("잘못된 요청");
        if (!fieldErrors.isEmpty()) {
            body.setProperty("errors", fieldErrors);
        }

        log.warn("[Validation] 요청 검증 실패: {}", fieldErrors);

        // 상태·헤더 처리를 상속 로직에 그대로 맡기고 본문만 바꿔 넘긴다.
        return handleExceptionInternal(ex, body, headers, status, request);
    }

    /**
     * 대화방 없음 / 소유자 불일치(404).
     */
    @ExceptionHandler(SessionNotFoundException.class)
    public ProblemDetail handleSessionNotFound(SessionNotFoundException ex) {
        log.warn("[NotFound] {}", ex.getMessage());

        // 예외 메시지에는 sessionId가 들어 있다. 요청자가 이미 보낸 값이므로 되돌려줘도 새어나갈 게 없다.
        ProblemDetail body = ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, ex.getMessage());
        body.setTitle("대화방을 찾을 수 없음");
        return body;
    }

    /**
     * AI 엔진 호출 실패(502).
     *
     * 실패를 200 OK로 위장하지 않는 것이 요점이다. 원인은 로그에만 남기고,
     * 사용자에게는 고정 문구를 준다. ex.getMessage()에는 내부 URL과 예외 클래스명이 들어 있다.
     */
    @ExceptionHandler(AiEngineException.class)
    public ProblemDetail handleAiEngine(AiEngineException ex) {
        log.error("[AiEngine] AI 엔진 호출 실패", ex);

        ProblemDetail body = ProblemDetail.forStatusAndDetail(
                HttpStatus.BAD_GATEWAY,
                "AI 검색 엔진에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도해주세요.");
        body.setTitle("AI 엔진 응답 실패");
        return body;
    }
}
