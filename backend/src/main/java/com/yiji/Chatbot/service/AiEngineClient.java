package com.yiji.Chatbot.service;

import com.yiji.Chatbot.dto.InternalChatDto;
import com.yiji.Chatbot.exception.AiEngineException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

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
                sessionId, query, request.history().size());

        InternalChatDto.Response response;
        try {
            response = aiEngineRestClient.post()
                    .uri("/internal/chat")
                    .contentType(MediaType.APPLICATION_JSON)
                    .body(request)
                    .retrieve()
                    .body(InternalChatDto.Response.class);
        } catch (RestClientException e) {
            // 실패를 답변처럼 반환하지 않는다. 그렇게 하면 오류 문구가 ASSISTANT 메시지로
            // DB와 Redis에 영구 저장되고, 다음 턴에 LLM 컨텍스트로 들어간다.
            throw new AiEngineException("AI 서버 호출 실패 (sessionId: " + sessionId + ")", e);
        }

        if (response == null) {
            throw new AiEngineException("AI 서버 응답 본문이 비어 있음 (sessionId: " + sessionId + ")");
        }

        log.info("[AiEngineClient] AI 서버 응답 성공 (sourcesCount: {})",
                response.sources() != null ? response.sources().size() : 0);
        return response;
    }
}
