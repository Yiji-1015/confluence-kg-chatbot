package com.yiji.Chatbot.dto;

import lombok.Builder;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 프론트엔드로 전달하는 채팅 답변 응답 DTO
 *
 * @param sessionId 확정/유지된 대화방 ID
 * @param answer    AI 챗봇 생성 답변
 * @param sources   답변 근거가 된 Confluence 출처 문서 목록
 * @param createdAt 응답 생성 시각
 */
@Builder
public record ChatResponseDto(
        String sessionId,
        String answer,
        List<SourceDocumentDto> sources,
        LocalDateTime createdAt
) {
}
