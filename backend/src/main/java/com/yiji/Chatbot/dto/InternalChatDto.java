package com.yiji.Chatbot.dto;

import lombok.Builder;

import java.util.List;

/**
 * Python AI Engine (POST /internal/chat) 통신 전용 DTO 모음
 */
public class InternalChatDto {

    private InternalChatDto() {
    }

    /**
     * @param role "user" 또는 "assistant"
     */
    @Builder
    public record MessageRole(String role, String content) {
    }

    @Builder
    public record Request(
            String sessionId,
            String query,
            List<MessageRole> history,
            String model
    ) {
    }

    @Builder
    public record SourceDocument(
            String documentId,
            String title,
            String url,
            String author,
            String category,
            Double score
    ) {
    }

    @Builder
    public record Response(
            String sessionId,
            String answer,
            List<SourceDocument> sources
    ) {
    }
}
