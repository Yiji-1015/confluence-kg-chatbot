package com.yiji.Chatbot.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 프론트엔드로 전달하는 채팅 답변 응답 DTO
 */
@Getter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ChatResponseDto {

    private String sessionId;              // 확정/유지된 대화방 ID (UUID)
    private String answer;                 // AI 챗봇 생성 답변
    private List<SourceDocumentDto> sources; // 답변 근거가 된 Confluence 출처 문서 목록
    private LocalDateTime createdAt;       // 응답 생성 시각
}
