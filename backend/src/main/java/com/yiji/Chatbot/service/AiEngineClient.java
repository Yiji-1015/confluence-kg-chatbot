package com.yiji.Chatbot.service;

import com.yiji.Chatbot.dto.InternalChatDto;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.Collections;
import java.util.List;

/**
 * Python AI Engine (FastAPI POST /internal/chat) 통신 클라이언트 서비스
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class AiEngineClient {

    private final RestClient aiEngineRestClient;

    /**
     * Python AI 서버로 RAG 질문 생성 요청 전송
     */
    public InternalChatDto.Response requestChat(
            String sessionId,
            String query,
            List<InternalChatDto.MessageRole> history
    ) {
        InternalChatDto.Request request = InternalChatDto.Request.builder()
                .sessionId(sessionId)
                .query(query)
                .history(history != null ? history : Collections.emptyList())
                .build();

        log.info("[AiEngineClient] AI 서버 호출 시작 (sessionId: {}, query: '{}', historySize: {})",
                sessionId, query, request.getHistory().size());

        try {
            InternalChatDto.Response response = aiEngineRestClient.post()
                    .uri("/internal/chat")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(request)
                    .retrieve()
                    .body(InternalChatDto.Response.class);

            if (response == null) {
                log.error("[AiEngineClient] AI 서버 응답이 null입니다.");
                return createFallbackResponse(sessionId, "AI 서버로부터 유효한 응답을 받지 못했습니다.");
            }

            log.info("[AiEngineClient] AI 서버 응답 성공 (sourcesCount: {})",
                    response.getSources() != null ? response.getSources().size() : 0);
            return response;

        } catch (Exception e) {
            log.error("[AiEngineClient] AI 서버 호출 중 예외 발생: {}", e.getMessage(), e);
            return createFallbackResponse(sessionId, "죄송합니다. AI 검색 엔진 서버와 일시적으로 통신할 수 없습니다: " + e.getMessage());
        }
    }

    /**
     * AI 서버 장애 발생 시 사용자에게 친절한 에러 메시지를 반환하는 Fallback 응답 생성
     */
    private InternalChatDto.Response createFallbackResponse(String sessionId, String errorMessage) {
        return InternalChatDto.Response.builder()
                .sessionId(sessionId)
                .answer(errorMessage)
                .sources(Collections.emptyList())
                .build();
    }
}
