package com.yiji.Chatbot.dto;

import lombok.Builder;

import java.time.LocalDateTime;

/**
 * 좌측 사이드바에 표시할 대화방 목록 1개 항목 DTO
 */
@Builder
public record ChatSessionDto(
        String id,
        String userId,
        String title,
        LocalDateTime createdAt,
        LocalDateTime updatedAt
) {
}
