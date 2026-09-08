package com.yiji.Chatbot.dto;

import lombok.Builder;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 대화 내역 1건을 나타내는 DTO
 *
 * @param role    "USER" 또는 "ASSISTANT"
 * @param sources RAG 출처 목록 (ASSISTANT 답변일 때만 존재)
 */
@Builder
public record ChatMessageDto(
        Long id,
        String role,
        String content,
        List<SourceDocumentDto> sources,
        LocalDateTime createdAt
) {
}
