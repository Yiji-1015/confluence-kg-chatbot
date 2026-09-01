package com.yiji.Chatbot.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * Python AI Engine (POST /internal/chat) 통신 전용 DTO 클래스 모음
 */
public class InternalChatDto {

    @Getter
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class MessageRole {
        private String role;    // "user" 또는 "assistant"
        private String content; // 대화 내용
    }

    @Getter
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class Request {
        private String sessionId;
        private String query;
        private List<MessageRole> history;
        private String model;
    }

    @Getter
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class SourceDocument {
        private String documentId;
        private String title;
        private String url;
        private String author;
        private String category;
        private Double score;
    }

    @Getter
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class Response {
        private String sessionId;
        private String answer;
        private List<SourceDocument> sources;
    }
}
